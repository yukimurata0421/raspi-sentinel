from __future__ import annotations

import logging
import subprocess
import time
from email.utils import parsedate_to_datetime
from typing import Any
from urllib import error, request

from ._version import __version__
from .checks import CheckResult
from .config import TargetConfig
from .state_helpers import safe_bool, safe_float
from .state_models import TargetState

LOG = logging.getLogger(__name__)


def _fetch_http_date_epoch(url: str, timeout_sec: int) -> tuple[float | None, str | None]:
    req = request.Request(
        url=url,
        method="HEAD",
        headers={"User-Agent": f"raspi-sentinel/{__version__}"},
    )
    try:
        with request.urlopen(req, timeout=timeout_sec) as response:
            date_raw = response.headers.get("Date")
    except error.HTTPError as exc:
        date_raw = exc.headers.get("Date") if exc.headers else None
        if date_raw is None:
            return None, f"http error status={exc.code}"
    except Exception as exc:
        return None, str(exc)

    if not date_raw:
        return None, "date header missing"

    try:
        dt = parsedate_to_datetime(date_raw)
    except Exception:
        return None, "date header parse failed"
    if dt.tzinfo is None:
        return None, "date header timezone missing"
    return dt.timestamp(), None


def _query_ntp_sync_ok(timeout_sec: int = 3) -> bool | None:
    try:
        result = subprocess.run(
            ["timedatectl", "show", "-p", "NTPSynchronized", "--value"],
            check=False,
            timeout=timeout_sec,
            capture_output=True,
            text=True,
        )
    except Exception:
        return None

    if result.returncode != 0:
        return None

    raw = result.stdout.strip().lower()
    if raw in ("true", "yes", "1"):
        return True
    if raw in ("false", "no", "0"):
        return False
    return None


def apply_time_health_checks(
    target: TargetConfig,
    target_state: TargetState | dict[str, Any],
    result: CheckResult,
    now_wall_ts: float | None = None,
    now_mono_ts: float | None = None,
) -> None:
    if not target.time_health_enabled:
        return

    wall_now = now_wall_ts if now_wall_ts is not None else time.time()
    mono_now = now_mono_ts if now_mono_ts is not None else time.monotonic()
    result.observations["wall_time_epoch"] = wall_now
    result.observations["monotonic_sec"] = mono_now
    result.observations["clock_skew_threshold_sec"] = target.clock_skew_threshold_sec

    if isinstance(target_state, TargetState):
        model = target_state
        raw_target_state: dict[str, Any] | None = None
    else:
        model = TargetState.from_dict(target_state)
        raw_target_state = target_state

    prev_wall = model.clock_prev_wall_time_epoch
    prev_mono = model.clock_prev_monotonic_sec
    freeze_detected = False
    jump_detected = False
    skew_detected = False
    insufficient_interval = False

    if prev_wall is not None and prev_mono is not None:
        delta_wall = wall_now - prev_wall
        delta_mono = mono_now - prev_mono
        drift = delta_wall - delta_mono

        result.observations["delta_wall_sec"] = delta_wall
        result.observations["delta_monotonic_sec"] = delta_mono
        result.observations["clock_drift_sec"] = drift

        if delta_mono < target.check_interval_threshold_sec:
            insufficient_interval = True
        elif (
            delta_mono >= target.wall_clock_freeze_min_monotonic_sec
            and delta_wall <= target.wall_clock_freeze_max_wall_advance_sec
        ):
            freeze_detected = True
        elif abs(drift) >= target.wall_clock_drift_threshold_sec:
            jump_detected = True
    else:
        insufficient_interval = True

    model.clock_prev_wall_time_epoch = wall_now
    model.clock_prev_monotonic_sec = mono_now

    http_probe_ok: bool | None = None
    if target.http_time_probe_url:
        http_epoch, probe_error = _fetch_http_date_epoch(
            url=target.http_time_probe_url,
            timeout_sec=target.http_time_probe_timeout_sec,
        )
        http_probe_ok = probe_error is None and http_epoch is not None
        result.observations["http_probe_ok"] = http_probe_ok
        if probe_error is not None:
            result.observations["http_probe_error"] = probe_error
        if http_epoch is not None:
            skew_sec = http_epoch - wall_now
            result.observations["http_date_epoch"] = http_epoch
            result.observations["http_time_skew_sec"] = skew_sec
            if abs(skew_sec) >= target.clock_skew_threshold_sec:
                skew_detected = True

    ntp_sync_ok = _query_ntp_sync_ok()
    if ntp_sync_ok is not None:
        result.observations["ntp_sync_ok"] = ntp_sync_ok

    if freeze_detected:
        consecutive_clock_freeze_count = model.consecutive_clock_freeze_count + 1
    else:
        consecutive_clock_freeze_count = 0
    model.consecutive_clock_freeze_count = consecutive_clock_freeze_count
    result.observations["consecutive_clock_freeze_count"] = consecutive_clock_freeze_count

    has_clock_anomaly = freeze_detected or jump_detected or skew_detected
    if has_clock_anomaly:
        clock_anomaly_consecutive = model.clock_anomaly_consecutive + 1
    else:
        clock_anomaly_consecutive = 0
    model.clock_anomaly_consecutive = clock_anomaly_consecutive
    result.observations["clock_anomaly_consecutive"] = clock_anomaly_consecutive

    dns_ok = safe_bool(result.observations.get("dns_ok"))
    gateway_ok = safe_bool(result.observations.get("gateway_ok"))
    if target.http_time_probe_url and http_probe_ok is None:
        http_probe_ok = safe_bool(result.observations.get("http_probe_ok"))
    skew_abs = abs(safe_float(result.observations.get("http_time_skew_sec")) or 0.0)

    clock_frozen_confirmed = (
        consecutive_clock_freeze_count >= target.clock_anomaly_reboot_consecutive
        and dns_ok is True
        and gateway_ok is True
        and http_probe_ok is True
        and skew_abs >= target.clock_skew_threshold_sec
    )
    result.observations["clock_frozen_detected"] = freeze_detected
    result.observations["clock_jump_detected"] = jump_detected
    result.observations["clock_skew_detected"] = skew_detected
    result.observations["insufficient_interval"] = insufficient_interval
    result.observations["clock_frozen_confirmed"] = clock_frozen_confirmed
    result.observations["clock_reboot_ready"] = clock_frozen_confirmed

    reason = "healthy"
    if freeze_detected:
        if clock_frozen_confirmed:
            reason = "clock_frozen_confirmed"
        elif consecutive_clock_freeze_count >= 2:
            reason = "clock_frozen_persistent"
        else:
            reason = "clock_frozen"
    elif jump_detected:
        reason = "clock_jump"
    elif skew_detected:
        if ntp_sync_ok is False:
            reason = "time_sync_broken_skewed"
        else:
            reason = "clock_skewed"
    elif http_probe_ok is False:
        reason = "http_probe_failed"
    elif gateway_ok is False:
        reason = "gateway_error"
    elif dns_ok is False and gateway_ok is True:
        reason = "dns_error"
    elif (
        ntp_sync_ok is False
        and target.http_time_probe_url
        and skew_abs < target.clock_skew_threshold_sec
    ):
        reason = "time_sync_broken"
    elif insufficient_interval:
        reason = "insufficient_interval"

    model.clock_last_reason = reason
    if raw_target_state is not None:
        model.merge_into(raw_target_state)
    result.observations["clock_reason"] = reason
