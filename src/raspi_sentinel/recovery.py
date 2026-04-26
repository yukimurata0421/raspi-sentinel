from __future__ import annotations

import logging
import subprocess
import time
from dataclasses import dataclass

from .checks import CheckResult
from .config import GlobalConfig, TargetConfig
from .state import StateStore
from .state_helpers import read_uptime_sec
from .state_models import GlobalState, RebootRecord, TargetState

LOG = logging.getLogger(__name__)

REBOOT_ALLOWED_POLICY_REASONS = frozenset(
    {
        "process_error",
        "external_status_failed",
        "clock_frozen_confirmed",
    }
)
NETWORK_ONLY_FAILED_REASONS = frozenset(
    {
        "link_error",
        "route_missing",
        "multi_factor_network_outage",
    }
)


@dataclass(slots=True)
class RecoveryOutcome:
    action: str
    requested_reboot: bool
    reboot_reason: str | None = None


def _thresholds(target: TargetConfig, global_config: GlobalConfig) -> tuple[int, int]:
    restart_threshold = target.restart_threshold or global_config.restart_threshold
    reboot_threshold = target.reboot_threshold or global_config.reboot_threshold
    if reboot_threshold < restart_threshold:
        reboot_threshold = restart_threshold
    return restart_threshold, reboot_threshold


def _record_action_model(model: TargetState, action: str, now_ts: float) -> None:
    model.last_action = action
    model.last_action_ts = now_ts


def _within_cooldown(last_ts: float | int | None, cooldown_sec: int, now_ts: float) -> bool:
    if cooldown_sec <= 0 or last_ts is None:
        return False
    try:
        delta = now_ts - float(last_ts)
    except (TypeError, ValueError):
        return False
    return delta < cooldown_sec


def _has_failure(result: CheckResult, check_name: str) -> bool:
    return any(f.check == check_name for f in result.failures)


def _has_non_dependency_failure(result: CheckResult) -> bool:
    return any(not f.check.startswith("dependency_") for f in result.failures)


def _clock_reboot_confirmed(result: CheckResult) -> bool:
    return result.observations.get("clock_frozen_confirmed") is True


def _policy_failed(result: CheckResult) -> bool:
    return result.observations.get("policy_status") == "failed"


def _policy_reason(result: CheckResult) -> str | None:
    reason = result.observations.get("policy_reason")
    return reason if isinstance(reason, str) else None


def network_only_failures_excluded_from_reboot() -> bool:
    """Return True when network-only failed reasons are excluded from reboot allowlist."""
    return NETWORK_ONLY_FAILED_REASONS.isdisjoint(REBOOT_ALLOWED_POLICY_REASONS)


def network_only_failures_can_reboot() -> bool:
    """Backward-compatibility helper; prefer network_only_failures_excluded_from_reboot()."""
    return not network_only_failures_excluded_from_reboot()


def _reboot_reason_allowed(result: CheckResult) -> bool:
    reason = _policy_reason(result)
    if reason is None:
        # Backward-compatible fallback for old state/tests that still populate
        # failures without policy_reason.
        return _has_non_dependency_failure(result)
    return reason in REBOOT_ALLOWED_POLICY_REASONS


def _can_reboot(global_config: GlobalConfig, state: GlobalState, now_ts: float) -> tuple[bool, str]:
    # Time-window boundaries are inclusive for reboot history filtering:
    # entries where (now_ts - entry.ts) <= reboot_window_sec remain in-window.
    # Cooldown checks stay strict (< cooldown) so exactly-equal timestamps reopen.
    uptime = read_uptime_sec()
    if uptime < global_config.min_uptime_for_reboot_sec:
        return (
            False,
            (
                "reboot blocked by uptime guard: "
                f"uptime={uptime:.0f}s min={global_config.min_uptime_for_reboot_sec}s"
            ),
        )

    filtered: list[RebootRecord] = []
    for entry in state.reboots:
        ts_f = entry.ts
        if now_ts - ts_f <= global_config.reboot_window_sec:
            filtered.append(entry)
    state.reboots = filtered

    if filtered:
        last_ts = filtered[-1].ts
        if _within_cooldown(last_ts, global_config.reboot_cooldown_sec, now_ts):
            return (
                False,
                (f"reboot blocked by cooldown: cooldown={global_config.reboot_cooldown_sec}s"),
            )

    if len(filtered) >= global_config.max_reboots_in_window:
        return (
            False,
            (
                "reboot blocked by window cap: "
                f"count={len(filtered)} window={global_config.reboot_window_sec}s "
                f"max={global_config.max_reboots_in_window}"
            ),
        )

    return True, "allowed"


def _restart_services(services: list[str], dry_run: bool) -> bool:
    if not services:
        LOG.warning("restart requested but no services configured")
        return False

    ok = True
    for service in services:
        if dry_run:
            LOG.warning("dry-run: would restart service '%s'", service)
            continue
        try:
            result = subprocess.run(
                ["systemctl", "restart", service],
                check=False,
                timeout=30,
                capture_output=True,
                text=True,
            )
        except subprocess.TimeoutExpired:
            LOG.error("service restart timeout: %s", service)
            ok = False
            continue
        except OSError as exc:
            LOG.error("cannot run systemctl restart for %s: %s", service, exc)
            ok = False
            continue

        if result.returncode != 0:
            detail = result.stderr.strip() or result.stdout.strip() or "unknown error"
            LOG.error("failed to restart service '%s': %s", service, detail)
            ok = False
        else:
            LOG.warning("restarted service '%s'", service)
    return ok


def _trigger_reboot(dry_run: bool, reason: str) -> bool:
    if dry_run:
        LOG.error("dry-run: would reboot system; reason=%s", reason)
        return True

    try:
        result = subprocess.run(
            ["systemctl", "reboot"],
            check=False,
            timeout=15,
            capture_output=True,
            text=True,
        )
    except subprocess.TimeoutExpired:
        LOG.error("systemctl reboot command timed out")
        return False
    except OSError as exc:
        LOG.error("cannot execute reboot command: %s", exc)
        return False

    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "unknown error"
        LOG.error("reboot command failed: %s", detail)
        return False

    return True


def execute_deferred_reboot(*, dry_run: bool, reason: str) -> bool:
    """Execute a reboot command after state has been durably persisted."""
    return _trigger_reboot(dry_run=dry_run, reason=reason)


def apply_recovery(
    target: TargetConfig,
    check_result: CheckResult,
    global_config: GlobalConfig,
    state: GlobalState,
    dry_run: bool,
    allow_disruptive_actions: bool = True,
    now_ts: float | None = None,
) -> RecoveryOutcome:
    ts = state.ensure_target(target.name)
    effective_now = time.time() if now_ts is None else now_ts
    clock_reboot_confirmed = _clock_reboot_confirmed(check_result)
    policy_failed = _policy_failed(check_result)

    def _return(
        action: str,
        *,
        reboot: bool = False,
        reboot_reason: str | None = None,
        record_action: bool = True,
    ) -> RecoveryOutcome:
        if record_action:
            _record_action_model(ts, action, effective_now)
        return RecoveryOutcome(action=action, requested_reboot=reboot, reboot_reason=reboot_reason)

    if check_result.healthy and not clock_reboot_confirmed:
        previous = ts.consecutive_failures
        ts.consecutive_failures = 0
        ts.last_healthy_ts = effective_now
        if previous > 0:
            LOG.info(
                "target '%s' recovered naturally, failure counter reset (%d -> 0)",
                target.name,
                previous,
            )
        return _return("none")

    failures_text = "; ".join(f"{f.check}: {f.message}" for f in check_result.failures).strip()
    if not failures_text:
        failures_text = str(check_result.observations.get("policy_reason", "unhealthy"))
    consecutive = ts.consecutive_failures + 1
    ts.consecutive_failures = consecutive
    ts.last_failure_ts = effective_now
    ts.last_failure_reason = failures_text

    if not allow_disruptive_actions:
        LOG.error(
            "target '%s': disruptive recovery actions disabled in limited mode; reason=%s",
            target.name,
            failures_text,
        )
        return _return("warn", record_action=False)

    if clock_reboot_confirmed:
        if not policy_failed:
            LOG.warning(
                (
                    "target '%s': confirmed clock reboot blocked because "
                    "policy_status is not failed (status=%s)"
                ),
                target.name,
                check_result.observations.get("policy_status"),
            )
            return _return("warn", record_action=False)

        can_reboot, guard_reason = _can_reboot(global_config, state, effective_now)
        if can_reboot:
            LOG.error(
                "target '%s': confirmed clock freeze anomaly; requesting reboot. reason=%s",
                target.name,
                failures_text,
            )
            StateStore.append_reboot_record(
                state,
                now_ts=effective_now,
                target=target.name,
                reason=failures_text,
            )
            return _return("reboot", reboot=True, reboot_reason=failures_text)
        else:
            LOG.error(
                "target '%s': confirmed clock reboot blocked by safeguard: %s",
                target.name,
                guard_reason,
            )
        return _return("warn", record_action=False)

    restart_threshold, reboot_threshold = _thresholds(target, global_config)
    LOG.warning(
        "target '%s' unhealthy (consecutive=%d, restart_threshold=%d, reboot_threshold=%d): %s",
        target.name,
        consecutive,
        restart_threshold,
        reboot_threshold,
        failures_text,
    )

    last_action = ts.last_action
    last_action_ts = ts.last_action_ts
    has_dns_failure = _has_failure(check_result, "dependency_dns")
    has_gateway_failure = _has_failure(check_result, "dependency_gateway")
    has_non_dependency_failure = _has_non_dependency_failure(check_result)
    # DNS-only dependency failures should not escalate to reboot.
    # See docs/principles/recovery-philosophy.md.
    if has_dns_failure and not has_gateway_failure and not has_non_dependency_failure:
        LOG.warning(
            (
                "target '%s': DNS-only dependency failure detected; "
                "skip restart/reboot and keep warning state"
            ),
            target.name,
        )
        return _return("warn", record_action=False)

    if consecutive >= reboot_threshold:
        if last_action == "restart" and _within_cooldown(
            last_action_ts,
            global_config.restart_cooldown_sec,
            effective_now,
        ):
            LOG.warning(
                (
                    "target '%s': reboot suppressed right after restart by "
                    "restart_cooldown_sec (%ss)"
                ),
                target.name,
                global_config.restart_cooldown_sec,
            )
        elif not policy_failed:
            LOG.warning(
                "target '%s': reboot blocked because policy_status is not failed (status=%s)",
                target.name,
                check_result.observations.get("policy_status"),
            )
        elif has_dns_failure and not has_gateway_failure:
            LOG.error(
                (
                    "target '%s': reboot blocked because failure is classified as "
                    "DNS-only dependency issue"
                ),
                target.name,
            )
        elif not _reboot_reason_allowed(check_result):
            LOG.error(
                (
                    "target '%s': reboot blocked because policy_reason is not in reboot allowlist "
                    "(policy_reason=%s)"
                ),
                target.name,
                _policy_reason(check_result),
            )
        else:
            can_reboot, guard_reason = _can_reboot(global_config, state, effective_now)
            if can_reboot:
                LOG.error(
                    "target '%s': reboot threshold reached. requesting reboot. reason=%s",
                    target.name,
                    failures_text,
                )
                StateStore.append_reboot_record(
                    state,
                    now_ts=effective_now,
                    target=target.name,
                    reason=failures_text,
                )
                return _return("reboot", reboot=True, reboot_reason=failures_text)
            else:
                LOG.error(
                    "target '%s': reboot blocked by safeguard: %s",
                    target.name,
                    guard_reason,
                )

    if consecutive >= restart_threshold:
        if last_action == "restart" and _within_cooldown(
            last_action_ts,
            global_config.restart_cooldown_sec,
            effective_now,
        ):
            LOG.warning(
                "target '%s': restart suppressed by cooldown (%ss)",
                target.name,
                global_config.restart_cooldown_sec,
            )
            return _return("warn", record_action=False)

        restarted = _restart_services(target.services, dry_run=dry_run)
        action = "restart" if restarted else "warn"
        return _return(action, record_action=(action != "warn"))

    return _return("warn", record_action=False)
