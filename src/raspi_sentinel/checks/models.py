from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, TypedDict

ObservationScalar = str | int | float | bool | None
ObservationMap = dict[str, ObservationScalar]


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
