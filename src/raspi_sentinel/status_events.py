from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from .checks import CheckResult
from .checks.models import (
    EVIDENCE_BOOL_FIELDS,
    EVIDENCE_FLOAT_FIELDS,
    EVIDENCE_INT_FIELDS,
    EVIDENCE_STRING_FIELDS,
    EVIDENCE_THRESHOLD_FLAGS,
    ObservationMap,
    copy_bool_or_none,
    copy_float_values,
    copy_int_values,
    copy_str_or_none,
)
from .policy import PolicySnapshot, classify_target_policy
from .state_helpers import maybe_rotate_file, safe_optional_int
from .state_models import TargetState

LOG = logging.getLogger(__name__)


def apply_policy_to_result(result: CheckResult, policy: PolicySnapshot) -> None:
    """Set policy_status / policy_reason; align ``result.healthy`` with ``policy.is_ok``."""
    result.observations["policy_status"] = policy.status
    result.observations["policy_reason"] = policy.reason
    result.observations["policy_subreason"] = policy.subreason
    result.healthy = policy.is_ok


def classify_target_state(
    result: CheckResult,
    target_state: TargetState | None = None,
) -> tuple[str, str]:
    p = classify_target_policy(result=result, target_state=target_state)
    return p.status, p.reason


def classify_target_status(
    result: CheckResult,
    target_state: TargetState | None = None,
) -> str:
    return classify_target_policy(result=result, target_state=target_state).status


def classify_target_reason(
    result: CheckResult,
    target_state: TargetState | None = None,
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
        events_file.parent.mkdir(parents=True, exist_ok=True)
        maybe_rotate_file(events_file, max_file_bytes, backup_generations=backup_generations)
        with events_file.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(event, sort_keys=True) + "\n")
    except OSError as exc:
        LOG.error("failed to append event to %s: %s", events_file, exc)


def build_event_evidence(result: CheckResult) -> ObservationMap:
    observations = result.observations
    payload: ObservationMap = {}
    copy_float_values(observations=observations, payload=payload, fields=EVIDENCE_FLOAT_FIELDS)
    copy_bool_or_none(observations=observations, payload=payload, fields=EVIDENCE_BOOL_FIELDS)
    copy_str_or_none(observations=observations, payload=payload, fields=EVIDENCE_STRING_FIELDS)
    copy_bool_or_none(observations=observations, payload=payload, fields=EVIDENCE_THRESHOLD_FLAGS)
    copy_int_values(observations=observations, payload=payload, fields=EVIDENCE_INT_FIELDS)

    for counter_name in (
        "link_fail_consecutive",
        "route_fail_consecutive",
        "gateway_fail_consecutive",
        "internet_fail_consecutive",
        "dns_fail_consecutive",
        "http_fail_consecutive",
    ):
        value = safe_optional_int(observations.get(counter_name))
        if value is not None:
            payload[counter_name] = value

    return payload


def record_status_events(
    events_file: Path,
    target_state: TargetState,
    target_name: str,
    current_status: str,
    current_reason: str,
    result: CheckResult,
    action: str,
    now_ts: float,
    max_file_bytes: int = 0,
    backup_generations: int = 1,
    current_subreason: str | None = None,
) -> None:
    previous_status = target_state.last_status or "unknown"
    previous_reason = target_state.last_reason or "unknown"
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
                **({"subreason": current_subreason} if current_subreason else {}),
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
                **({"subreason": current_subreason} if current_subreason else {}),
                **evidence,
            },
            max_file_bytes=max_file_bytes,
            backup_generations=backup_generations,
        )

    target_state.last_status = current_status
    target_state.last_reason = current_reason
