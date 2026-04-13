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
from .state_helpers import safe_bool, safe_float, safe_int
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
        result.observations["http_time_probe_ok"] = http_probe_ok
        if not isinstance(result.observations.get("http_probe_ok"), bool):
            result.observations["http_probe_ok"] = http_probe_ok
        if probe_error is not None:
            result.observations["http_time_probe_error"] = probe_error
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

    def _update_counter(counter_name: str, current_state: bool | None) -> int:
        key = f"network_{counter_name}_failures"
        current = safe_int(model.extra.get(key), 0) or 0
        if current_state is False:
            current += 1
        elif current_state is True:
            current = 0
        model.extra[key] = current
        return current

    dns_ok = safe_bool(result.observations.get("dns_ok"))
    dns_server_reachable = safe_bool(result.observations.get("dns_server_reachable"))
    link_ok = safe_bool(result.observations.get("link_ok"))
    default_route_ok = safe_bool(result.observations.get("default_route_ok"))
    gateway_ok = safe_bool(result.observations.get("gateway_ok"))
    internet_ip_ok = safe_bool(result.observations.get("internet_ip_ok"))
    wan_vs_target_ok = safe_bool(result.observations.get("wan_vs_target_ok"))
    if target.http_time_probe_url and http_probe_ok is None:
        http_probe_ok = safe_bool(result.observations.get("http_probe_ok"))
    skew_abs = abs(safe_float(result.observations.get("http_time_skew_sec")) or 0.0)

    if result.observations.get("network_probe_enabled") is True:
        dns_layer_ok: bool | None
        if dns_ok is False or dns_server_reachable is False:
            dns_layer_ok = False
        elif dns_ok is True:
            dns_layer_ok = True
        else:
            dns_layer_ok = None

        result.observations["link_fail_consecutive"] = _update_counter("link", link_ok)
        result.observations["route_fail_consecutive"] = _update_counter("route", default_route_ok)
        result.observations["gateway_fail_consecutive"] = _update_counter("gateway", gateway_ok)
        result.observations["internet_fail_consecutive"] = _update_counter(
            "internet", internet_ip_ok
        )
        result.observations["dns_fail_consecutive"] = _update_counter("dns", dns_layer_ok)
        result.observations["http_fail_consecutive"] = _update_counter("http", http_probe_ok)
        result.observations["network_degraded_threshold"] = (
            target.consecutive_failure_thresholds.get("degraded", 2)
        )
        result.observations["network_failed_threshold"] = target.consecutive_failure_thresholds.get(
            "failed", 6
        )

        gateway_latency = safe_float(result.observations.get("gateway_latency_ms"))
        internet_latency = safe_float(result.observations.get("internet_ip_latency_ms"))
        dns_latency = safe_float(result.observations.get("dns_latency_ms"))
        http_latency = safe_float(result.observations.get("http_total_latency_ms"))
        gateway_loss = safe_float(result.observations.get("gateway_packet_loss_pct"))
        internet_loss = safe_float(result.observations.get("internet_ip_packet_loss_pct"))
        gateway_latency_threshold = target.latency_thresholds_ms.get("gateway")
        internet_latency_threshold = target.latency_thresholds_ms.get("internet_ip")
        dns_latency_threshold = target.latency_thresholds_ms.get("dns")
        http_latency_threshold = target.latency_thresholds_ms.get("http_total")
        gateway_loss_threshold = target.packet_loss_thresholds_pct.get("gateway")
        internet_loss_threshold = target.packet_loss_thresholds_pct.get("internet_ip")
        result.observations["gateway_latency_exceeded"] = bool(
            gateway_latency is not None
            and gateway_latency_threshold is not None
            and gateway_latency > gateway_latency_threshold
        )
        result.observations["internet_latency_exceeded"] = bool(
            internet_latency is not None
            and internet_latency_threshold is not None
            and internet_latency > internet_latency_threshold
        )
        result.observations["dns_latency_exceeded"] = bool(
            dns_latency is not None
            and dns_latency_threshold is not None
            and dns_latency > dns_latency_threshold
        )
        result.observations["http_latency_exceeded"] = bool(
            http_latency is not None
            and http_latency_threshold is not None
            and http_latency > http_latency_threshold
        )
        result.observations["gateway_loss_exceeded"] = bool(
            gateway_loss is not None
            and gateway_loss_threshold is not None
            and gateway_loss > gateway_loss_threshold
        )
        result.observations["internet_loss_exceeded"] = bool(
            internet_loss is not None
            and internet_loss_threshold is not None
            and internet_loss > internet_loss_threshold
        )

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
        reason = "http_error"
    elif link_ok is False:
        reason = "link_error"
    elif default_route_ok is False:
        reason = "route_missing"
    elif gateway_ok is False:
        reason = "gateway_error"
    elif internet_ip_ok is False and gateway_ok is True:
        reason = "wan_error"
    elif dns_server_reachable is False and internet_ip_ok is True:
        reason = "dns_server_error"
    elif dns_ok is False and gateway_ok is True:
        reason = "dns_error"
    elif wan_vs_target_ok is False:
        reason = "target_reachability_error"
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
