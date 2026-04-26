from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Literal

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
from .config import AppConfig
from .contracts import STATS_SCHEMA_VERSION
from .state_helpers import safe_int, safe_optional_int, write_json_atomic
from .state_models import GlobalState
from .status_events import classify_target_reason, classify_target_status

LOG = logging.getLogger(__name__)


def build_monitor_stats_snapshot(
    config: AppConfig,
    state: GlobalState,
    target_results: dict[str, CheckResult],
    now_ts: float,
) -> dict[str, object]:
    ts_text = datetime.fromtimestamp(now_ts).astimezone().isoformat(timespec="seconds")
    state_targets = state.targets

    targets_payload: dict[str, object] = {}
    counts: dict[Literal["ok", "degraded", "failed"], int] = {
        "ok": 0,
        "degraded": 0,
        "failed": 0,
    }

    for target in config.targets:
        result = target_results.get(target.name)
        if result is None:
            target_state = state_targets.get(target.name)
            if target_state is None:
                status = "unknown"
                reason = "unknown"
            else:
                status = target_state.last_status
                reason = target_state.last_reason
        else:
            state_for_target = state_targets.get(target.name)
            status = classify_target_status(result=result, target_state=state_for_target)
            reason = classify_target_reason(result=result, target_state=state_for_target)

        if status in counts:
            counts[status] += 1
        target_state = state_targets.get(target.name)
        last_action = target_state.last_action if target_state is not None else "unknown"
        last_failure_reason = target_state.last_failure_reason if target_state is not None else ""
        consecutive_failures = target_state.consecutive_failures if target_state is not None else 0

        payload: dict[str, object] = {
            "status": status,
            "reason": reason,
            "last_action": str(last_action),
            "consecutive_failures": safe_int(consecutive_failures, 0),
            "last_failure_reason": str(last_failure_reason),
        }
        if result is not None:
            policy_subreason = result.observations.get("policy_subreason")
            if isinstance(policy_subreason, str):
                payload["subreason"] = policy_subreason
            clock_reason = result.observations.get("clock_reason")
            if isinstance(clock_reason, str):
                payload["clock_reason"] = clock_reason

            clock_anomaly_consecutive = safe_optional_int(
                result.observations.get("clock_anomaly_consecutive")
            )
            if clock_anomaly_consecutive is not None:
                payload["clock_anomaly_consecutive"] = clock_anomaly_consecutive

            http_time_skew = result.observations.get("http_time_skew_sec")
            if isinstance(http_time_skew, (int, float)):
                payload["http_time_skew_sec"] = float(http_time_skew)

            ntp_sync_ok = result.observations.get("ntp_sync_ok")
            if isinstance(ntp_sync_ok, bool):
                payload["ntp_sync_ok"] = ntp_sync_ok

            obs_payload: ObservationMap = {}
            copy_bool_or_none(
                observations=result.observations,
                payload=obs_payload,
                fields=EVIDENCE_BOOL_FIELDS + EVIDENCE_THRESHOLD_FLAGS,
            )
            copy_str_or_none(
                observations=result.observations,
                payload=obs_payload,
                fields=EVIDENCE_STRING_FIELDS,
            )
            copy_float_values(
                observations=result.observations,
                payload=obs_payload,
                fields=EVIDENCE_FLOAT_FIELDS,
            )
            copy_int_values(
                observations=result.observations,
                payload=obs_payload,
                fields=EVIDENCE_INT_FIELDS,
            )
            payload.update(obs_payload)

        targets_payload[target.name] = payload

    if counts.get("failed", 0) > 0:
        overall_status = "failed"
    elif counts.get("degraded", 0) > 0:
        overall_status = "degraded"
    elif counts.get("ok", 0) > 0:
        overall_status = "ok"
    else:
        overall_status = "unknown"

    return {
        "stats_schema_version": STATS_SCHEMA_VERSION,
        "service": "raspi-sentinel",
        "updated_at": ts_text,
        "status": overall_status,
        "targets_total": len(config.targets),
        "targets_ok": counts.get("ok", 0),
        "targets_degraded": counts.get("degraded", 0),
        "targets_failed": counts.get("failed", 0),
        "targets": targets_payload,
    }


def maybe_write_monitor_stats(
    config: AppConfig,
    state: GlobalState,
    target_results: dict[str, CheckResult],
    now_ts: float,
) -> None:
    interval_sec = config.global_config.monitor_stats_interval_sec
    last_written_ts = state.monitor_stats.last_written_ts
    elapsed: float
    if last_written_ts is None:
        elapsed = float(interval_sec)
    else:
        elapsed = now_ts - last_written_ts

    snapshot = build_monitor_stats_snapshot(
        config=config,
        state=state,
        target_results=target_results,
        now_ts=now_ts,
    )
    signature_payload = dict(snapshot)
    signature_payload.pop("updated_at", None)
    signature = json.dumps(signature_payload, sort_keys=True, separators=(",", ":"))
    previous_signature = state.monitor_stats.last_snapshot_signature

    should_write = elapsed >= interval_sec or previous_signature != signature
    if not should_write:
        return

    if write_json_atomic(config.global_config.monitor_stats_file, snapshot, indent=2):
        state.monitor_stats.last_written_ts = now_ts
        state.monitor_stats.last_snapshot_signature = signature
