from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from .checks import CheckResult
from .policy import PolicySnapshot, classify_target_policy
from .state_helpers import maybe_rotate_file, safe_bool, safe_float, safe_optional_int
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


def build_event_evidence(result: CheckResult) -> dict[str, Any]:
    observations = result.observations
    payload: dict[str, Any] = {}
    for field_name in (
        "delta_wall_sec",
        "delta_monotonic_sec",
        "clock_drift_sec",
        "http_time_skew_sec",
        "stats_age_sec",
        "dns_latency_ms",
        "external_status_updated_age_sec",
        "external_last_progress_age_sec",
        "external_last_success_age_sec",
    ):
        value = safe_float(observations.get(field_name))
        if value is not None:
            payload[field_name] = value

    for field_name in (
        "link_ok",
        "iface_up",
        "wifi_associated",
        "ip_assigned",
        "default_route_ok",
        "gateway_ok",
        "neighbor_resolved",
        "arp_gateway_ok",
        "internet_ip_ok",
        "dns_server_reachable",
        "dns_ok",
        "wan_vs_target_ok",
        "http_probe_ok",
        "ntp_sync_ok",
    ):
        if field_name not in observations:
            continue
        raw = observations.get(field_name)
        value = safe_bool(raw)
        if value is not None:
            payload[field_name] = value
        elif raw is None:
            payload[field_name] = None

    freeze_count = safe_optional_int(observations.get("consecutive_clock_freeze_count"))
    if freeze_count is not None:
        payload["consecutive_clock_freeze_count"] = freeze_count

    nullable_fields = (
        "network_interface",
        "operstate_raw",
        "ssid",
        "bssid",
        "rssi_dbm",
        "tx_bitrate_mbps",
        "rx_bitrate_mbps",
        "default_route_iface",
        "gateway_ip",
        "route_table_snapshot",
        "gateway_latency_ms",
        "gateway_packet_loss_pct",
        "internet_ip_target",
        "internet_ip_latency_ms",
        "internet_ip_packet_loss_pct",
        "dns_server",
        "dns_query_target",
        "dns_error_kind",
        "route_error_kind",
        "gateway_error_kind",
        "wan_error_kind",
        "http_probe_target",
        "http_status_code",
        "http_total_latency_ms",
        "http_connect_latency_ms",
        "http_tls_latency_ms",
        "http_error_kind",
        "gateway_latency_exceeded",
        "internet_latency_exceeded",
        "dns_latency_exceeded",
        "http_latency_exceeded",
        "gateway_loss_exceeded",
        "internet_loss_exceeded",
        "link_fail_consecutive",
        "route_fail_consecutive",
        "gateway_fail_consecutive",
        "internet_fail_consecutive",
        "dns_fail_consecutive",
        "http_fail_consecutive",
        "external_internal_state",
        "external_reason",
    )
    for field_name in nullable_fields:
        if field_name not in observations:
            continue
        raw_value = observations.get(field_name)
        if raw_value is None or isinstance(raw_value, (bool, int, float, str)):
            payload[field_name] = raw_value

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
