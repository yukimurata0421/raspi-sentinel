from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, cast

from .checks import CheckResult
from .checks.models import Observations
from .state_helpers import safe_bool, safe_float, safe_int
from .state_models import TargetState

PolicyStatus = Literal["ok", "degraded", "failed"]

# Failures from these checks are treated as hard "process" failures (failed / process_error).
PROCESS_CHECK_NAMES = frozenset({"service_active", "command", "heartbeat_file", "output_file"})
NETWORK_DEPENDENCY_CHECK_NAMES = frozenset(
    {
        "dependency_link",
        "dependency_default_route",
        "dependency_gateway",
        "dependency_internet_ip",
        "dependency_dns_server",
        "dependency_dns",
        "dependency_wan_target",
        "dependency_http_probe",
    }
)


@dataclass(frozen=True, slots=True)
class PolicySnapshot:
    """Semantic health classification: single source of truth for status/reason in a cycle."""

    status: PolicyStatus
    reason: str
    subreason: str | None = None

    @property
    def is_ok(self) -> bool:
        return self.status == "ok"


def classify_target_policy(
    result: CheckResult,
    target_state: TargetState | None = None,
) -> PolicySnapshot:
    checks = {failure.check for failure in result.failures}
    observations = cast(Observations, result.observations)
    previous_reason = ""
    if target_state is not None:
        previous_reason = target_state.last_reason or ""

    dns_ok = safe_bool(observations.get("dns_ok"))
    dns_server_reachable = safe_bool(observations.get("dns_server_reachable"))
    link_ok = safe_bool(observations.get("link_ok"))
    default_route_ok = safe_bool(observations.get("default_route_ok"))
    gateway_ok = safe_bool(observations.get("gateway_ok"))
    internet_ip_ok = safe_bool(observations.get("internet_ip_ok"))
    wan_vs_target_ok = safe_bool(observations.get("wan_vs_target_ok"))
    http_probe_ok = safe_bool(observations.get("http_probe_ok"))
    ntp_sync_ok = safe_bool(observations.get("ntp_sync_ok"))
    insufficient_interval = observations.get("insufficient_interval") is True
    clock_frozen_detected = observations.get("clock_frozen_detected") is True
    clock_jump_detected = observations.get("clock_jump_detected") is True
    clock_skew_detected = observations.get("clock_skew_detected") is True
    clock_frozen_confirmed = observations.get("clock_frozen_confirmed") is True
    consecutive_freeze_count = safe_int(observations.get("consecutive_clock_freeze_count"), 0) or 0
    skew_abs = abs(safe_float(observations.get("http_time_skew_sec")) or 0.0)
    skew_threshold = safe_float(observations.get("clock_skew_threshold_sec")) or 300.0
    network_probe_enabled = observations.get("network_probe_enabled") is True
    degraded_threshold = safe_int(observations.get("network_degraded_threshold"), 2) or 2
    failed_threshold = safe_int(observations.get("network_failed_threshold"), 6) or 6
    link_failures = safe_int(observations.get("link_fail_consecutive"), 0) or 0
    route_failures = safe_int(observations.get("route_fail_consecutive"), 0) or 0
    gateway_failures = safe_int(observations.get("gateway_fail_consecutive"), 0) or 0
    internet_failures = safe_int(observations.get("internet_fail_consecutive"), 0) or 0
    dns_failures = safe_int(observations.get("dns_fail_consecutive"), 0) or 0
    http_failures = safe_int(observations.get("http_fail_consecutive"), 0) or 0
    network_checks_remaining = checks - NETWORK_DEPENDENCY_CHECK_NAMES
    route_error_kind = observations.get("route_error_kind")
    gateway_error_kind = observations.get("gateway_error_kind")
    wan_error_kind = observations.get("wan_error_kind")

    def _persistent_fail(observation_value: bool | None, counter: int) -> bool:
        return observation_value is False and counter >= degraded_threshold

    if clock_frozen_confirmed:
        return PolicySnapshot("failed", "clock_frozen_confirmed")

    # Prioritize clock anomaly interpretation over stale external-status signals.
    if clock_frozen_detected:
        if consecutive_freeze_count >= 2:
            return PolicySnapshot("degraded", "clock_frozen_persistent")
        return PolicySnapshot("degraded", "clock_frozen")
    if clock_jump_detected:
        return PolicySnapshot("degraded", "clock_jump")
    if clock_skew_detected:
        if ntp_sync_ok is False:
            return PolicySnapshot("degraded", "time_sync_broken_skewed")
        return PolicySnapshot("degraded", "clock_skewed")

    if "semantic_external_internal_state" in checks:
        internal_state = str(observations.get("external_internal_state") or "").lower()
        if internal_state == "failed":
            return PolicySnapshot("failed", "external_status_failed")
        return PolicySnapshot("degraded", "external_status_unhealthy")

    if "semantic_external_status_file" in checks or "semantic_external_updated_at" in checks:
        return PolicySnapshot("degraded", "external_status_stale")

    if "semantic_external_last_progress_ts" in checks:
        return PolicySnapshot("degraded", "external_progress_stall")

    if "semantic_external_last_success_ts" in checks:
        return PolicySnapshot("degraded", "external_success_stale")

    if "semantic_updated_at" in checks or "semantic_stats_file" in checks:
        return PolicySnapshot("degraded", "stats_stale")

    if network_probe_enabled:
        if link_ok is False and link_failures >= failed_threshold:
            return PolicySnapshot("failed", "link_error")
        if default_route_ok is False and route_failures >= failed_threshold:
            return PolicySnapshot(
                "failed",
                "route_missing",
                route_error_kind if isinstance(route_error_kind, str) else None,
            )
        if (
            gateway_ok is False
            and internet_ip_ok is False
            and dns_ok is False
            and http_probe_ok is False
            and gateway_failures >= failed_threshold
            and internet_failures >= failed_threshold
            and dns_failures >= failed_threshold
            and http_failures >= failed_threshold
        ):
            return PolicySnapshot("failed", "multi_factor_network_outage")

        if _persistent_fail(link_ok, link_failures):
            return PolicySnapshot("degraded", "link_error")
        if _persistent_fail(default_route_ok, route_failures):
            return PolicySnapshot(
                "degraded",
                "route_missing",
                route_error_kind if isinstance(route_error_kind, str) else None,
            )
        if _persistent_fail(gateway_ok, gateway_failures) and link_ok is True:
            return PolicySnapshot(
                "degraded",
                "gateway_error",
                gateway_error_kind if isinstance(gateway_error_kind, str) else None,
            )
        if _persistent_fail(internet_ip_ok, internet_failures) and gateway_ok is True:
            return PolicySnapshot(
                "degraded",
                "wan_error",
                wan_error_kind if isinstance(wan_error_kind, str) else None,
            )
        has_dns_persistent_failure = _persistent_fail(
            dns_server_reachable, dns_failures
        ) or _persistent_fail(dns_ok, dns_failures)
        if has_dns_persistent_failure and internet_ip_ok is True:
            return PolicySnapshot("degraded", "dns_error")
        if _persistent_fail(http_probe_ok, http_failures) and dns_ok is True:
            return PolicySnapshot("degraded", "http_error")
        if wan_vs_target_ok is False and internet_ip_ok is True:
            return PolicySnapshot("degraded", "target_reachability_error")

        if observations.get("gateway_latency_exceeded") is True:
            return PolicySnapshot("degraded", "gateway_latency_high")
        if observations.get("internet_latency_exceeded") is True:
            return PolicySnapshot("degraded", "wan_latency_high")
        if observations.get("gateway_loss_exceeded") is True:
            return PolicySnapshot("degraded", "gateway_packet_loss")
        if observations.get("internet_loss_exceeded") is True:
            return PolicySnapshot("degraded", "wan_packet_loss")
        if observations.get("dns_latency_exceeded") is True:
            return PolicySnapshot("degraded", "dns_latency_high")
        if observations.get("http_latency_exceeded") is True:
            return PolicySnapshot("degraded", "http_latency_high")

        has_transient_network_failure = any(
            [
                link_ok is False and 0 < link_failures < degraded_threshold,
                default_route_ok is False and 0 < route_failures < degraded_threshold,
                gateway_ok is False and 0 < gateway_failures < degraded_threshold,
                internet_ip_ok is False and 0 < internet_failures < degraded_threshold,
                dns_ok is False and 0 < dns_failures < degraded_threshold,
                http_probe_ok is False and 0 < http_failures < degraded_threshold,
            ]
        )
        if has_transient_network_failure:
            return PolicySnapshot("ok", "transient_network_failure")
    else:
        if "dependency_link" in checks or link_ok is False:
            return PolicySnapshot("degraded", "link_error")
        if "dependency_default_route" in checks or default_route_ok is False:
            return PolicySnapshot("degraded", "route_missing")
        if "dependency_gateway" in checks or gateway_ok is False:
            return PolicySnapshot("degraded", "gateway_error")
        if ("dependency_internet_ip" in checks and "dependency_gateway" not in checks) or (
            internet_ip_ok is False and gateway_ok is True
        ):
            return PolicySnapshot("degraded", "wan_error")
        if ("dependency_dns_server" in checks and "dependency_internet_ip" not in checks) or (
            dns_server_reachable is False and internet_ip_ok is True
        ):
            return PolicySnapshot("degraded", "dns_server_error")
        if ("dependency_dns" in checks and "dependency_gateway" not in checks) or (
            dns_ok is False and gateway_ok is True
        ):
            return PolicySnapshot("degraded", "dns_error")
        if "dependency_wan_target" in checks or wan_vs_target_ok is False:
            return PolicySnapshot("degraded", "target_reachability_error")

    if http_probe_ok is False:
        return PolicySnapshot("degraded", "http_error")

    if ntp_sync_ok is False and skew_abs < skew_threshold:
        return PolicySnapshot("degraded", "time_sync_broken")

    if insufficient_interval and not network_checks_remaining:
        return PolicySnapshot("ok", "insufficient_interval")

    if previous_reason == "clock_jump":
        return PolicySnapshot("ok", "recovered_from_clock_jump")
    if (
        previous_reason in ("clock_skewed", "time_sync_broken_skewed", "http_error")
        and http_probe_ok is True
        and skew_abs < skew_threshold
    ):
        return PolicySnapshot("ok", "recovered_from_clock_skew")

    if network_checks_remaining:
        if network_checks_remaining & PROCESS_CHECK_NAMES:
            return PolicySnapshot("failed", "process_error")
        return PolicySnapshot("degraded", "unhealthy")

    return PolicySnapshot("ok", "healthy")
