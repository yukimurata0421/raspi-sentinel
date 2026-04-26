from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, TypedDict

ObservationScalar = str | int | float | bool | None
ObservationMap = dict[str, ObservationScalar]

PROCESS_CHECK_NAMES = frozenset({"service_active", "command", "heartbeat_file", "output_file"})

NETWORK_PROBE_INIT_FIELDS: tuple[str, ...] = (
    "link_ok",
    "iface_up",
    "wifi_associated",
    "ip_assigned",
    "operstate_raw",
    "default_route_ok",
    "gateway_ok",
    "internet_ip_ok",
    "dns_ok",
    "http_probe_ok",
    "arp_gateway_ok",
    "neighbor_resolved",
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
    "dns_latency_ms",
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
)

EVIDENCE_BOOL_FIELDS: tuple[str, ...] = (
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
)

EVIDENCE_STRING_FIELDS: tuple[str, ...] = (
    "network_interface",
    "operstate_raw",
    "ssid",
    "bssid",
    "default_route_iface",
    "gateway_ip",
    "route_table_snapshot",
    "internet_ip_target",
    "dns_server",
    "dns_query_target",
    "dns_error_kind",
    "route_error_kind",
    "gateway_error_kind",
    "wan_error_kind",
    "http_probe_target",
    "http_error_kind",
    "external_internal_state",
    "external_reason",
)

EVIDENCE_FLOAT_FIELDS: tuple[str, ...] = (
    "delta_wall_sec",
    "delta_monotonic_sec",
    "clock_drift_sec",
    "http_time_skew_sec",
    "stats_age_sec",
    "dns_latency_ms",
    "external_status_updated_age_sec",
    "external_last_progress_age_sec",
    "external_last_success_age_sec",
    "rssi_dbm",
    "tx_bitrate_mbps",
    "rx_bitrate_mbps",
    "gateway_latency_ms",
    "gateway_packet_loss_pct",
    "internet_ip_latency_ms",
    "internet_ip_packet_loss_pct",
    "http_total_latency_ms",
    "http_connect_latency_ms",
    "http_tls_latency_ms",
)

EVIDENCE_INT_FIELDS: tuple[str, ...] = (
    "http_status_code",
    "consecutive_clock_freeze_count",
    "clock_anomaly_consecutive",
)

EVIDENCE_THRESHOLD_FLAGS: tuple[str, ...] = (
    "gateway_latency_exceeded",
    "internet_latency_exceeded",
    "dns_latency_exceeded",
    "http_latency_exceeded",
    "gateway_loss_exceeded",
    "internet_loss_exceeded",
)


class Observations(TypedDict, total=False):
    network_probe_enabled: bool
    network_interface: str
    link_ok: bool | None
    iface_up: bool | None
    wifi_associated: bool | None
    ip_assigned: bool | None
    default_route_ok: bool | None
    gateway_ok: bool | None
    internet_ip_ok: bool | None
    dns_ok: bool | None
    dns_server_reachable: bool | None
    wan_vs_target_ok: bool | None
    http_probe_ok: bool | None
    ntp_sync_ok: bool | None
    clock_frozen_detected: bool
    clock_jump_detected: bool
    clock_skew_detected: bool
    clock_frozen_confirmed: bool
    insufficient_interval: bool
    consecutive_clock_freeze_count: int
    http_time_skew_sec: float
    clock_skew_threshold_sec: float
    network_degraded_threshold: int
    network_failed_threshold: int
    link_fail_consecutive: int
    route_fail_consecutive: int
    gateway_fail_consecutive: int
    internet_fail_consecutive: int
    dns_fail_consecutive: int
    http_fail_consecutive: int
    route_error_kind: str | None
    gateway_error_kind: str | None
    wan_error_kind: str | None
    dns_error_kind: str | None
    http_error_kind: str | None
    http_probe_target: str | None
    http_status_code: int | None
    http_total_latency_ms: float | None
    http_connect_latency_ms: float | None
    http_tls_latency_ms: float | None
    gateway_latency_exceeded: bool
    internet_latency_exceeded: bool
    dns_latency_exceeded: bool
    http_latency_exceeded: bool
    gateway_loss_exceeded: bool
    internet_loss_exceeded: bool
    stats_status: str
    stats_age_sec: float
    records_processed_total: int
    external_internal_state: str
    external_reason: str


ObservationBooleanFlag = Literal[
    "gateway_latency_exceeded",
    "internet_latency_exceeded",
    "dns_latency_exceeded",
    "http_latency_exceeded",
    "gateway_loss_exceeded",
    "internet_loss_exceeded",
]


def is_observation_flag_true(observations: Observations, key: ObservationBooleanFlag) -> bool:
    return observations.get(key) is True


@dataclass(slots=True)
class CheckFailure:
    check: str
    message: str


@dataclass(slots=True)
class CheckResult:
    target: str
    healthy: bool
    failures: list[CheckFailure]
    observations: ObservationMap = field(default_factory=dict)


def copy_bool_or_none(
    *,
    observations: ObservationMap,
    payload: ObservationMap,
    fields: tuple[str, ...],
) -> None:
    for field_name in fields:
        if field_name not in observations:
            continue
        raw = observations.get(field_name)
        if isinstance(raw, bool) or raw is None:
            payload[field_name] = raw


def copy_str_or_none(
    *,
    observations: ObservationMap,
    payload: ObservationMap,
    fields: tuple[str, ...],
) -> None:
    for field_name in fields:
        if field_name not in observations:
            continue
        raw = observations.get(field_name)
        if isinstance(raw, str) or raw is None:
            payload[field_name] = raw


def copy_float_values(
    *,
    observations: ObservationMap,
    payload: ObservationMap,
    fields: tuple[str, ...],
) -> None:
    for field_name in fields:
        if field_name not in observations:
            continue
        raw = observations.get(field_name)
        if isinstance(raw, (int, float)):
            payload[field_name] = float(raw)


def copy_int_values(
    *,
    observations: ObservationMap,
    payload: ObservationMap,
    fields: tuple[str, ...],
) -> None:
    for field_name in fields:
        if field_name not in observations:
            continue
        raw = observations.get(field_name)
        if isinstance(raw, int):
            payload[field_name] = raw
