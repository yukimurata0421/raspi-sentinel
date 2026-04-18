from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, cast

from .checks import CheckResult
from .checks.models import Observations, is_observation_flag_true
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


@dataclass(frozen=True, slots=True)
class PolicyContext:
    checks: set[str]
    observations: Observations
    previous_reason: str


@dataclass(frozen=True, slots=True)
class NetworkSignals:
    dns_ok: bool | None
    dns_server_reachable: bool | None
    link_ok: bool | None
    default_route_ok: bool | None
    gateway_ok: bool | None
    internet_ip_ok: bool | None
    wan_vs_target_ok: bool | None
    http_probe_ok: bool | None
    degraded_threshold: int
    failed_threshold: int
    link_failures: int
    route_failures: int
    gateway_failures: int
    internet_failures: int
    dns_failures: int
    http_failures: int
    route_error_kind: str | None
    gateway_error_kind: str | None
    wan_error_kind: str | None


@dataclass(frozen=True, slots=True)
class ClockSignals:
    ntp_sync_ok: bool | None
    insufficient_interval: bool
    frozen_detected: bool
    jump_detected: bool
    skew_detected: bool
    frozen_confirmed: bool
    consecutive_freeze_count: int
    skew_abs: float
    skew_threshold: float


def _build_network_signals(observations: Observations) -> NetworkSignals:
    return NetworkSignals(
        dns_ok=safe_bool(observations.get("dns_ok")),
        dns_server_reachable=safe_bool(observations.get("dns_server_reachable")),
        link_ok=safe_bool(observations.get("link_ok")),
        default_route_ok=safe_bool(observations.get("default_route_ok")),
        gateway_ok=safe_bool(observations.get("gateway_ok")),
        internet_ip_ok=safe_bool(observations.get("internet_ip_ok")),
        wan_vs_target_ok=safe_bool(observations.get("wan_vs_target_ok")),
        http_probe_ok=safe_bool(observations.get("http_probe_ok")),
        degraded_threshold=safe_int(observations.get("network_degraded_threshold"), 2) or 2,
        failed_threshold=safe_int(observations.get("network_failed_threshold"), 6) or 6,
        link_failures=safe_int(observations.get("link_fail_consecutive"), 0) or 0,
        route_failures=safe_int(observations.get("route_fail_consecutive"), 0) or 0,
        gateway_failures=safe_int(observations.get("gateway_fail_consecutive"), 0) or 0,
        internet_failures=safe_int(observations.get("internet_fail_consecutive"), 0) or 0,
        dns_failures=safe_int(observations.get("dns_fail_consecutive"), 0) or 0,
        http_failures=safe_int(observations.get("http_fail_consecutive"), 0) or 0,
        route_error_kind=(
            observations.get("route_error_kind")
            if isinstance(observations.get("route_error_kind"), str)
            else None
        ),
        gateway_error_kind=(
            observations.get("gateway_error_kind")
            if isinstance(observations.get("gateway_error_kind"), str)
            else None
        ),
        wan_error_kind=(
            observations.get("wan_error_kind")
            if isinstance(observations.get("wan_error_kind"), str)
            else None
        ),
    )


def _build_clock_signals(observations: Observations) -> ClockSignals:
    skew_abs = abs(safe_float(observations.get("http_time_skew_sec")) or 0.0)
    skew_threshold = safe_float(observations.get("clock_skew_threshold_sec")) or 300.0
    return ClockSignals(
        ntp_sync_ok=safe_bool(observations.get("ntp_sync_ok")),
        insufficient_interval=observations.get("insufficient_interval") is True,
        frozen_detected=observations.get("clock_frozen_detected") is True,
        jump_detected=observations.get("clock_jump_detected") is True,
        skew_detected=observations.get("clock_skew_detected") is True,
        frozen_confirmed=observations.get("clock_frozen_confirmed") is True,
        consecutive_freeze_count=safe_int(observations.get("consecutive_clock_freeze_count"), 0)
        or 0,
        skew_abs=skew_abs,
        skew_threshold=skew_threshold,
    )


def _clock_policy(clock: ClockSignals) -> PolicySnapshot | None:
    if clock.frozen_confirmed:
        return PolicySnapshot("failed", "clock_frozen_confirmed")
    if clock.frozen_detected:
        if clock.consecutive_freeze_count >= 2:
            return PolicySnapshot("degraded", "clock_frozen_persistent")
        return PolicySnapshot("degraded", "clock_frozen")
    if clock.jump_detected:
        return PolicySnapshot("degraded", "clock_jump")
    if clock.skew_detected:
        if clock.ntp_sync_ok is False:
            return PolicySnapshot("degraded", "time_sync_broken_skewed")
        return PolicySnapshot("degraded", "clock_skewed")
    return None


def _external_policy(ctx: PolicyContext) -> PolicySnapshot | None:
    checks = ctx.checks
    observations = ctx.observations
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

    return None


def _persistent_fail(value: bool | None, counter: int, threshold: int) -> bool:
    return value is False and counter >= threshold


def _network_policy_enabled(ctx: PolicyContext, net: NetworkSignals) -> PolicySnapshot | None:
    observations = ctx.observations

    if net.link_ok is False and net.link_failures >= net.failed_threshold:
        return PolicySnapshot("failed", "link_error")
    if net.default_route_ok is False and net.route_failures >= net.failed_threshold:
        return PolicySnapshot("failed", "route_missing", net.route_error_kind)
    if (
        net.gateway_ok is False
        and net.internet_ip_ok is False
        and net.dns_ok is False
        and net.http_probe_ok is False
        and net.gateway_failures >= net.failed_threshold
        and net.internet_failures >= net.failed_threshold
        and net.dns_failures >= net.failed_threshold
        and net.http_failures >= net.failed_threshold
    ):
        return PolicySnapshot("failed", "multi_factor_network_outage")

    if _persistent_fail(net.link_ok, net.link_failures, net.degraded_threshold):
        return PolicySnapshot("degraded", "link_error")
    if _persistent_fail(net.default_route_ok, net.route_failures, net.degraded_threshold):
        return PolicySnapshot("degraded", "route_missing", net.route_error_kind)
    if (
        _persistent_fail(net.gateway_ok, net.gateway_failures, net.degraded_threshold)
        and net.link_ok is True
    ):
        return PolicySnapshot("degraded", "gateway_error", net.gateway_error_kind)
    if (
        _persistent_fail(net.internet_ip_ok, net.internet_failures, net.degraded_threshold)
        and net.gateway_ok is True
    ):
        return PolicySnapshot("degraded", "wan_error", net.wan_error_kind)

    has_dns_persistent_failure = _persistent_fail(
        net.dns_server_reachable, net.dns_failures, net.degraded_threshold
    ) or _persistent_fail(net.dns_ok, net.dns_failures, net.degraded_threshold)
    if has_dns_persistent_failure and net.internet_ip_ok is True:
        return PolicySnapshot("degraded", "dns_error")
    if (
        _persistent_fail(net.http_probe_ok, net.http_failures, net.degraded_threshold)
        and net.dns_ok is True
    ):
        return PolicySnapshot("degraded", "http_error")
    if net.wan_vs_target_ok is False and net.internet_ip_ok is True:
        return PolicySnapshot("degraded", "target_reachability_error")

    if is_observation_flag_true(observations, "gateway_latency_exceeded"):
        return PolicySnapshot("degraded", "gateway_latency_high")
    if is_observation_flag_true(observations, "internet_latency_exceeded"):
        return PolicySnapshot("degraded", "wan_latency_high")
    if is_observation_flag_true(observations, "gateway_loss_exceeded"):
        return PolicySnapshot("degraded", "gateway_packet_loss")
    if is_observation_flag_true(observations, "internet_loss_exceeded"):
        return PolicySnapshot("degraded", "wan_packet_loss")
    if is_observation_flag_true(observations, "dns_latency_exceeded"):
        return PolicySnapshot("degraded", "dns_latency_high")
    if is_observation_flag_true(observations, "http_latency_exceeded"):
        return PolicySnapshot("degraded", "http_latency_high")

    has_transient_network_failure = any(
        [
            net.link_ok is False and 0 < net.link_failures < net.degraded_threshold,
            net.default_route_ok is False and 0 < net.route_failures < net.degraded_threshold,
            net.gateway_ok is False and 0 < net.gateway_failures < net.degraded_threshold,
            net.internet_ip_ok is False and 0 < net.internet_failures < net.degraded_threshold,
            net.dns_ok is False and 0 < net.dns_failures < net.degraded_threshold,
            net.http_probe_ok is False and 0 < net.http_failures < net.degraded_threshold,
        ]
    )
    if has_transient_network_failure:
        return PolicySnapshot("ok", "transient_network_failure")

    return None


def _network_policy_disabled(ctx: PolicyContext, net: NetworkSignals) -> PolicySnapshot | None:
    checks = ctx.checks
    if "dependency_link" in checks or net.link_ok is False:
        return PolicySnapshot("degraded", "link_error")
    if "dependency_default_route" in checks or net.default_route_ok is False:
        return PolicySnapshot("degraded", "route_missing")
    if "dependency_gateway" in checks or net.gateway_ok is False:
        return PolicySnapshot("degraded", "gateway_error")
    if ("dependency_internet_ip" in checks and "dependency_gateway" not in checks) or (
        net.internet_ip_ok is False and net.gateway_ok is True
    ):
        return PolicySnapshot("degraded", "wan_error")
    if ("dependency_dns_server" in checks and "dependency_internet_ip" not in checks) or (
        net.dns_server_reachable is False and net.internet_ip_ok is True
    ):
        return PolicySnapshot("degraded", "dns_server_error")
    if ("dependency_dns" in checks and "dependency_gateway" not in checks) or (
        net.dns_ok is False and net.gateway_ok is True
    ):
        return PolicySnapshot("degraded", "dns_error")
    if "dependency_wan_target" in checks or net.wan_vs_target_ok is False:
        return PolicySnapshot("degraded", "target_reachability_error")
    return None


def _fallback_policy(
    ctx: PolicyContext,
    net: NetworkSignals,
    clock: ClockSignals,
    network_checks_remaining: set[str],
) -> PolicySnapshot:
    if net.http_probe_ok is False:
        return PolicySnapshot("degraded", "http_error")

    if clock.ntp_sync_ok is False and clock.skew_abs < clock.skew_threshold:
        return PolicySnapshot("degraded", "time_sync_broken")

    if clock.insufficient_interval and not network_checks_remaining:
        return PolicySnapshot("ok", "insufficient_interval")

    if ctx.previous_reason == "clock_jump":
        return PolicySnapshot("ok", "recovered_from_clock_jump")

    if (
        ctx.previous_reason in ("clock_skewed", "time_sync_broken_skewed", "http_error")
        and net.http_probe_ok is True
        and clock.skew_abs < clock.skew_threshold
    ):
        return PolicySnapshot("ok", "recovered_from_clock_skew")

    if network_checks_remaining:
        if network_checks_remaining & PROCESS_CHECK_NAMES:
            return PolicySnapshot("failed", "process_error")
        return PolicySnapshot("degraded", "unhealthy")

    return PolicySnapshot("ok", "healthy")


def classify_target_policy(
    result: CheckResult,
    target_state: TargetState | None = None,
) -> PolicySnapshot:
    checks = {failure.check for failure in result.failures}
    observations = cast(Observations, result.observations)
    previous_reason = target_state.last_reason or "" if target_state is not None else ""

    ctx = PolicyContext(checks=checks, observations=observations, previous_reason=previous_reason)
    clock = _build_clock_signals(observations)
    net = _build_network_signals(observations)

    policy = _clock_policy(clock)
    if policy is not None:
        return policy

    policy = _external_policy(ctx)
    if policy is not None:
        return policy

    network_probe_enabled = observations.get("network_probe_enabled") is True
    if network_probe_enabled:
        policy = _network_policy_enabled(ctx, net)
    else:
        policy = _network_policy_disabled(ctx, net)
    if policy is not None:
        return policy

    network_checks_remaining = checks - NETWORK_DEPENDENCY_CHECK_NAMES
    return _fallback_policy(ctx, net, clock, network_checks_remaining)
