from __future__ import annotations

from datetime import datetime
import json
import logging
from pathlib import Path
from typing import Any

from .checks import CheckResult

LOG = logging.getLogger(__name__)


def _safe_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    return None


def _safe_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _safe_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def classify_target_state(
    result: CheckResult,
    target_state: dict[str, Any] | None = None,
) -> tuple[str, str]:
    checks = {failure.check for failure in result.failures}
    observations = result.observations
    previous_reason = ""
    if isinstance(target_state, dict):
        previous_reason = str(target_state.get("last_reason", "") or "")

    dns_ok = _safe_bool(observations.get("dns_ok"))
    gateway_ok = _safe_bool(observations.get("gateway_ok"))
    http_probe_ok = _safe_bool(observations.get("http_probe_ok"))
    ntp_sync_ok = _safe_bool(observations.get("ntp_sync_ok"))
    insufficient_interval = observations.get("insufficient_interval") is True
    clock_frozen_detected = observations.get("clock_frozen_detected") is True
    clock_jump_detected = observations.get("clock_jump_detected") is True
    clock_skew_detected = observations.get("clock_skew_detected") is True
    clock_frozen_confirmed = observations.get("clock_frozen_confirmed") is True
    consecutive_freeze_count = _safe_int(observations.get("consecutive_clock_freeze_count")) or 0
    skew_abs = abs(_safe_float(observations.get("http_time_skew_sec")) or 0.0)
    skew_threshold = _safe_float(observations.get("clock_skew_threshold_sec")) or 300.0

    if clock_frozen_confirmed:
        return "failed", "clock_frozen_confirmed"

    if "semantic_updated_at" in checks or "semantic_stats_file" in checks:
        return "degraded", "stats_stale"

    if "dependency_gateway" in checks or gateway_ok is False:
        return "degraded", "gateway_error"
    if ("dependency_dns" in checks and "dependency_gateway" not in checks) or (
        dns_ok is False and gateway_ok is True
    ):
        return "degraded", "dns_error"

    if clock_frozen_detected:
        if consecutive_freeze_count >= 2:
            return "degraded", "clock_frozen_persistent"
        return "degraded", "clock_frozen"
    if clock_jump_detected:
        return "degraded", "clock_jump"
    if clock_skew_detected:
        if ntp_sync_ok is False:
            return "degraded", "time_sync_broken_skewed"
        return "degraded", "clock_skewed"

    if http_probe_ok is False:
        return "degraded", "http_probe_failed"

    if ntp_sync_ok is False and skew_abs < skew_threshold:
        return "degraded", "time_sync_broken"

    if insufficient_interval and not checks:
        return "ok", "insufficient_interval"

    if previous_reason == "clock_jump":
        return "ok", "recovered_from_clock_jump"
    if (
        previous_reason in ("clock_skewed", "time_sync_broken_skewed", "http_probe_failed")
        and http_probe_ok is True
        and skew_abs < skew_threshold
    ):
        return "ok", "recovered_from_clock_skew"

    if checks:
        if "service_active" in checks or "command" in checks or "heartbeat_file" in checks or "output_file" in checks:
            return "failed", "process_error"
        return "degraded", "unhealthy"

    return "ok", "healthy"


def classify_target_status(
    result: CheckResult,
    target_state: dict[str, Any] | None = None,
) -> str:
    status, _ = classify_target_state(result=result, target_state=target_state)
    return status


def classify_target_reason(
    result: CheckResult,
    target_state: dict[str, Any] | None = None,
) -> str:
    _, reason = classify_target_state(result=result, target_state=target_state)
    return reason


def append_event(events_file: Path, event: dict[str, Any]) -> None:
    try:
        events_file.parent.mkdir(parents=True, exist_ok=True)
        with events_file.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(event, sort_keys=True) + "\n")
    except OSError as exc:
        LOG.error("failed to append event to %s: %s", events_file, exc)


def _build_event_evidence(result: CheckResult) -> dict[str, Any]:
    observations = result.observations
    payload: dict[str, Any] = {}
    for field_name in (
        "delta_wall_sec",
        "delta_monotonic_sec",
        "clock_drift_sec",
        "http_time_skew_sec",
        "stats_age_sec",
    ):
        value = _safe_float(observations.get(field_name))
        if value is not None:
            payload[field_name] = value

    for field_name in ("dns_ok", "gateway_ok", "http_probe_ok", "ntp_sync_ok"):
        value = _safe_bool(observations.get(field_name))
        if value is not None:
            payload[field_name] = value

    freeze_count = _safe_int(observations.get("consecutive_clock_freeze_count"))
    if freeze_count is not None:
        payload["consecutive_clock_freeze_count"] = freeze_count

    return payload


def record_status_events(
    events_file: Path,
    target_state: dict[str, Any],
    target_name: str,
    current_status: str,
    current_reason: str,
    result: CheckResult,
    action: str,
    now_ts: float,
) -> None:
    previous_status_raw = target_state.get("last_status")
    previous_status = previous_status_raw if isinstance(previous_status_raw, str) else "unknown"
    previous_reason_raw = target_state.get("last_reason")
    previous_reason = previous_reason_raw if isinstance(previous_reason_raw, str) else "unknown"
    ts_text = datetime.fromtimestamp(now_ts).astimezone().isoformat(timespec="seconds")
    evidence = _build_event_evidence(result)

    if previous_status != current_status or previous_reason != current_reason:
        append_event(
            events_file=events_file,
            event={
                "ts": ts_text,
                "service": target_name,
                "from": previous_status,
                "to": current_status,
                "reason": current_reason,
                **evidence,
            },
        )

    if action in ("restart", "reboot"):
        append_event(
            events_file=events_file,
            event={
                "ts": ts_text,
                "service": target_name,
                "from": current_status,
                "to": current_status,
                "reason": current_reason,
                "action": action,
                **evidence,
            },
        )

    target_state["last_status"] = current_status
    target_state["last_reason"] = current_reason
