from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from .checks import CheckResult
from .state_helpers import safe_bool, safe_float, safe_int
from .state_models import TargetState

PolicyStatus = Literal["ok", "degraded", "failed"]

# Failures from these checks are treated as hard "process" failures (failed / process_error).
PROCESS_CHECK_NAMES = frozenset({"service_active", "command", "heartbeat_file", "output_file"})


@dataclass(frozen=True, slots=True)
class PolicySnapshot:
    """Semantic health classification: single source of truth for status/reason in a cycle."""

    status: PolicyStatus
    reason: str

    @property
    def is_ok(self) -> bool:
        return self.status == "ok"


def classify_target_policy(
    result: CheckResult,
    target_state: TargetState | dict[str, Any] | None = None,
) -> PolicySnapshot:
    checks = {failure.check for failure in result.failures}
    observations = result.observations
    previous_reason = ""
    if isinstance(target_state, TargetState):
        previous_reason = target_state.last_reason or ""
    elif isinstance(target_state, dict):
        previous_reason = str(target_state.get("last_reason", "") or "")

    dns_ok = safe_bool(observations.get("dns_ok"))
    gateway_ok = safe_bool(observations.get("gateway_ok"))
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

    if clock_frozen_confirmed:
        return PolicySnapshot("failed", "clock_frozen_confirmed")

    if "semantic_updated_at" in checks or "semantic_stats_file" in checks:
        return PolicySnapshot("degraded", "stats_stale")

    if "dependency_gateway" in checks or gateway_ok is False:
        return PolicySnapshot("degraded", "gateway_error")
    if ("dependency_dns" in checks and "dependency_gateway" not in checks) or (
        dns_ok is False and gateway_ok is True
    ):
        return PolicySnapshot("degraded", "dns_error")

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

    if http_probe_ok is False:
        return PolicySnapshot("degraded", "http_probe_failed")

    if ntp_sync_ok is False and skew_abs < skew_threshold:
        return PolicySnapshot("degraded", "time_sync_broken")

    if insufficient_interval and not checks:
        return PolicySnapshot("ok", "insufficient_interval")

    if previous_reason == "clock_jump":
        return PolicySnapshot("ok", "recovered_from_clock_jump")
    if (
        previous_reason in ("clock_skewed", "time_sync_broken_skewed", "http_probe_failed")
        and http_probe_ok is True
        and skew_abs < skew_threshold
    ):
        return PolicySnapshot("ok", "recovered_from_clock_skew")

    if checks:
        if checks & PROCESS_CHECK_NAMES:
            return PolicySnapshot("failed", "process_error")
        return PolicySnapshot("degraded", "unhealthy")

    return PolicySnapshot("ok", "healthy")
