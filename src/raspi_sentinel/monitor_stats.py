from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from .checks import CheckFailure, CheckResult
from .config import AppConfig
from .runtime_state import safe_int, safe_optional_int
from .status_events import classify_target_reason, classify_target_status

LOG = logging.getLogger(__name__)


def apply_records_progress_check(
    target: Any,
    target_state: dict[str, Any],
    result: CheckResult,
) -> None:
    stall_cycles_threshold = target.stats_records_stall_cycles
    if stall_cycles_threshold is None:
        return

    current_records = safe_optional_int(result.observations.get("records_processed_total"))
    if current_records is None:
        return

    previous_records = safe_optional_int(target_state.get("last_records_processed_total"))
    stalled_cycles = safe_int(target_state.get("records_stalled_cycles"), 0)

    if previous_records is None or current_records < previous_records:
        stalled_cycles = 0
    elif current_records == previous_records:
        stalled_cycles += 1
        if stalled_cycles >= stall_cycles_threshold:
            result.failures.append(
                CheckFailure(
                    "semantic_records_stalled",
                    (
                        "records_processed_total is not increasing: "
                        f"value={current_records} stalled_cycles={stalled_cycles} "
                        f"threshold={stall_cycles_threshold}"
                    ),
                )
            )
    else:
        stalled_cycles = 0

    target_state["last_records_processed_total"] = current_records
    target_state["records_stalled_cycles"] = stalled_cycles
    result.healthy = not result.failures


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> bool:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        text = json.dumps(payload, sort_keys=True, indent=2)
        tmp_path.write_text(text + "\n", encoding="utf-8")
        tmp_path.replace(path)
    except OSError as exc:
        LOG.error("failed to write monitor stats %s: %s", path, exc)
        return False
    return True


def build_monitor_stats_snapshot(
    config: AppConfig,
    state: dict[str, Any],
    target_results: dict[str, CheckResult],
    now_ts: float,
) -> dict[str, Any]:
    ts_text = datetime.fromtimestamp(now_ts).astimezone().isoformat(timespec="seconds")
    state_targets = state.get("targets", {})
    if not isinstance(state_targets, dict):
        state_targets = {}

    targets_payload: dict[str, Any] = {}
    counts: dict[str, int] = {
        "ok": 0,
        "degraded": 0,
        "failed": 0,
        "unknown": 0,
    }

    for target in config.targets:
        result = target_results.get(target.name)
        if result is None:
            target_state = state_targets.get(target.name, {})
            status = str(target_state.get("last_status", "unknown"))
            reason = str(target_state.get("last_reason", "unknown"))
        else:
            status = classify_target_status(
                result=result, target_state=state_targets.get(target.name, {})
            )
            reason = classify_target_reason(
                result=result, target_state=state_targets.get(target.name, {})
            )

        counts[status] = counts.get(status, 0) + 1
        target_state = state_targets.get(target.name, {})
        if not isinstance(target_state, dict):
            target_state = {}

        payload: dict[str, Any] = {
            "status": status,
            "reason": reason,
            "last_action": str(target_state.get("last_action", "unknown")),
            "consecutive_failures": safe_int(target_state.get("consecutive_failures"), 0),
            "last_failure_reason": str(target_state.get("last_failure_reason", "")),
        }
        if result is not None:
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
    state: dict[str, Any],
    target_results: dict[str, CheckResult],
    now_ts: float,
) -> None:
    monitor_state = state.setdefault("monitor_stats", {})
    if not isinstance(monitor_state, dict):
        monitor_state = {}
        state["monitor_stats"] = monitor_state

    interval_sec = config.global_config.monitor_stats_interval_sec
    last_written_ts = monitor_state.get("last_written_ts")
    try:
        elapsed = now_ts - float(last_written_ts)
    except (TypeError, ValueError):
        elapsed = interval_sec

    snapshot = build_monitor_stats_snapshot(
        config=config,
        state=state,
        target_results=target_results,
        now_ts=now_ts,
    )
    signature_payload = dict(snapshot)
    signature_payload.pop("updated_at", None)
    signature = json.dumps(signature_payload, sort_keys=True, separators=(",", ":"))
    previous_signature = monitor_state.get("last_snapshot_signature")

    should_write = elapsed >= interval_sec or previous_signature != signature
    if not should_write:
        return

    if _write_json_atomic(config.global_config.monitor_stats_file, snapshot):
        monitor_state["last_written_ts"] = now_ts
        monitor_state["last_snapshot_signature"] = signature
