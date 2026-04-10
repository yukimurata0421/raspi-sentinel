from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from .checks import CheckResult
from .policy import PolicySnapshot, classify_target_policy
from .state_helpers import maybe_rotate_file, safe_bool, safe_float, safe_optional_int

LOG = logging.getLogger(__name__)


def apply_policy_to_result(result: CheckResult, policy: PolicySnapshot) -> None:
    """Set policy_status / policy_reason; align ``result.healthy`` with ``policy.is_ok``."""
    result.observations["policy_status"] = policy.status
    result.observations["policy_reason"] = policy.reason
    result.healthy = policy.is_ok


def classify_target_state(
    result: CheckResult,
    target_state: dict[str, Any] | None = None,
) -> tuple[str, str]:
    p = classify_target_policy(result=result, target_state=target_state)
    return p.status, p.reason


def classify_target_status(
    result: CheckResult,
    target_state: dict[str, Any] | None = None,
) -> str:
    return classify_target_policy(result=result, target_state=target_state).status


def classify_target_reason(
    result: CheckResult,
    target_state: dict[str, Any] | None = None,
) -> str:
    return classify_target_policy(result=result, target_state=target_state).reason


def record_notify_failure_event(
    events_file: Path,
    max_file_bytes: int,
    backup_generations: int,
    context: str,
    now_ts: float,
) -> None:
    ts_text = datetime.fromtimestamp(now_ts).astimezone().isoformat(timespec="seconds")
    append_event(
        events_file,
        {
            "ts": ts_text,
            "kind": "notify_delivery_failed",
            "context": context,
        },
        max_file_bytes=max_file_bytes,
        backup_generations=backup_generations,
    )


def append_event(
    events_file: Path,
    event: dict[str, Any],
    max_file_bytes: int = 0,
    backup_generations: int = 1,
) -> None:
    try:
        maybe_rotate_file(events_file, max_file_bytes, backup_generations=backup_generations)
        events_file.parent.mkdir(parents=True, exist_ok=True)
        with events_file.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(event, sort_keys=True) + "\n")
    except OSError as exc:
        LOG.error("failed to append event to %s: %s", events_file, exc)


def build_event_evidence(result: CheckResult) -> dict[str, Any]:
    observations = result.observations
    payload: dict[str, Any] = {}
    for field_name in (
        "delta_wall_sec",
        "delta_monotonic_sec",
        "clock_drift_sec",
        "http_time_skew_sec",
        "stats_age_sec",
    ):
        value = safe_float(observations.get(field_name))
        if value is not None:
            payload[field_name] = value

    for field_name in ("dns_ok", "gateway_ok", "http_probe_ok", "ntp_sync_ok"):
        value = safe_bool(observations.get(field_name))
        if value is not None:
            payload[field_name] = value

    freeze_count = safe_optional_int(observations.get("consecutive_clock_freeze_count"))
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
    max_file_bytes: int = 0,
    backup_generations: int = 1,
) -> None:
    previous_status_raw = target_state.get("last_status")
    previous_status = previous_status_raw if isinstance(previous_status_raw, str) else "unknown"
    previous_reason_raw = target_state.get("last_reason")
    previous_reason = previous_reason_raw if isinstance(previous_reason_raw, str) else "unknown"
    ts_text = datetime.fromtimestamp(now_ts).astimezone().isoformat(timespec="seconds")
    evidence = build_event_evidence(result)

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
            max_file_bytes=max_file_bytes,
            backup_generations=backup_generations,
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
            max_file_bytes=max_file_bytes,
            backup_generations=backup_generations,
        )

    target_state["last_status"] = current_status
    target_state["last_reason"] = current_reason
