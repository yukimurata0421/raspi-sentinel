from __future__ import annotations

import logging
import subprocess
import time
from dataclasses import dataclass
from typing import Any

from .checks import CheckResult
from .config import GlobalConfig, TargetConfig
from .state_helpers import target_state as get_target_dict
from .state_models import TargetState

LOG = logging.getLogger(__name__)

CLOCK_FAILURE_CHECKS = frozenset(
    {
        "semantic_clock_frozen",
        "semantic_clock_jump",
        "semantic_clock_skew",
    }
)


@dataclass(slots=True)
class RecoveryOutcome:
    action: str
    requested_reboot: bool


def _get_uptime_sec() -> float:
    try:
        with open("/proc/uptime", "r", encoding="utf-8") as fh:
            first = fh.read().split()[0]
        return float(first)
    except Exception:
        return 0.0


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


def _is_clock_only_failure(result: CheckResult) -> bool:
    if not result.failures:
        return False
    return all(f.check in CLOCK_FAILURE_CHECKS for f in result.failures)


def _clock_reboot_ready(result: CheckResult) -> bool:
    return result.observations.get("clock_reboot_ready") is True


def _clock_reboot_confirmed(result: CheckResult) -> bool:
    return result.observations.get("clock_frozen_confirmed") is True


def _can_reboot(
    global_config: GlobalConfig, state: dict[str, Any], now_ts: float
) -> tuple[bool, str]:
    uptime = _get_uptime_sec()
    if uptime < global_config.min_uptime_for_reboot_sec:
        return (
            False,
            (
                "reboot blocked by uptime guard: "
                f"uptime={uptime:.0f}s min={global_config.min_uptime_for_reboot_sec}s"
            ),
        )

    reboots = state.setdefault("reboots", [])
    filtered = []
    for entry in reboots:
        if not isinstance(entry, dict):
            continue
        ts = entry.get("ts")
        try:
            ts_f = float(ts)
        except (TypeError, ValueError):
            continue
        if now_ts - ts_f <= global_config.reboot_window_sec:
            filtered.append(entry)
    state["reboots"] = filtered

    if filtered:
        last_ts = float(filtered[-1]["ts"])
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


def apply_recovery(
    target: TargetConfig,
    check_result: CheckResult,
    global_config: GlobalConfig,
    state: dict[str, Any],
    dry_run: bool,
    allow_disruptive_actions: bool = True,
    now_ts: float | None = None,
) -> RecoveryOutcome:
    raw = get_target_dict(state, target.name)
    ts = TargetState.from_dict(raw)
    effective_now = time.time() if now_ts is None else now_ts
    clock_reboot_confirmed = _clock_reboot_confirmed(check_result)

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
        _record_action_model(ts, "none", effective_now)
        ts.merge_into(raw)
        return RecoveryOutcome(action="none", requested_reboot=False)

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
        _record_action_model(ts, "warn", effective_now)
        ts.merge_into(raw)
        return RecoveryOutcome(action="warn", requested_reboot=False)

    if clock_reboot_confirmed:
        can_reboot, guard_reason = _can_reboot(global_config, state, effective_now)
        if can_reboot:
            LOG.error(
                "target '%s': confirmed clock freeze anomaly; requesting reboot. reason=%s",
                target.name,
                failures_text,
            )
            reboot_ok = _trigger_reboot(dry_run=dry_run, reason=failures_text)
            if reboot_ok:
                reboots = state.setdefault("reboots", [])
                reboots.append(
                    {
                        "ts": effective_now,
                        "target": target.name,
                        "reason": failures_text,
                    }
                )
                _record_action_model(ts, "reboot", effective_now)
                ts.merge_into(raw)
                return RecoveryOutcome(action="reboot", requested_reboot=True)
            LOG.error("target '%s': confirmed clock reboot request failed", target.name)
        else:
            LOG.error(
                "target '%s': confirmed clock reboot blocked by safeguard: %s",
                target.name,
                guard_reason,
            )
        _record_action_model(ts, "warn", effective_now)
        ts.merge_into(raw)
        return RecoveryOutcome(action="warn", requested_reboot=False)

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
        _record_action_model(ts, "warn", effective_now)
        ts.merge_into(raw)
        return RecoveryOutcome(action="warn", requested_reboot=False)

    if consecutive >= reboot_threshold:
        if has_dns_failure and not has_gateway_failure:
            LOG.error(
                (
                    "target '%s': reboot blocked because failure is classified as "
                    "DNS-only dependency issue"
                ),
                target.name,
            )
        # Clock-only anomalies must pass additional persistence/dependency evidence before reboot.
        elif _is_clock_only_failure(check_result) and not _clock_reboot_ready(check_result):
            LOG.error(
                (
                    "target '%s': reboot blocked because clock anomaly is not persistent "
                    "or dependency confirmation is incomplete"
                ),
                target.name,
            )
        else:
            can_reboot, guard_reason = _can_reboot(global_config, state, effective_now)
            if can_reboot:
                LOG.error(
                    "target '%s': reboot threshold reached. requesting reboot. reason=%s",
                    target.name,
                    failures_text,
                )
                reboot_ok = _trigger_reboot(dry_run=dry_run, reason=failures_text)
                if reboot_ok:
                    reboots = state.setdefault("reboots", [])
                    reboots.append(
                        {
                            "ts": effective_now,
                            "target": target.name,
                            "reason": failures_text,
                        }
                    )
                    _record_action_model(ts, "reboot", effective_now)
                    ts.merge_into(raw)
                    return RecoveryOutcome(action="reboot", requested_reboot=True)
                LOG.error(
                    "target '%s': reboot request failed, falling back to restart path",
                    target.name,
                )
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
            _record_action_model(ts, "warn", effective_now)
            ts.merge_into(raw)
            return RecoveryOutcome(action="warn", requested_reboot=False)

        restarted = _restart_services(target.services, dry_run=dry_run)
        action = "restart" if restarted else "warn"
        _record_action_model(ts, action, effective_now)
        ts.merge_into(raw)
        return RecoveryOutcome(action=action, requested_reboot=False)

    _record_action_model(ts, "warn", effective_now)
    ts.merge_into(raw)
    return RecoveryOutcome(action="warn", requested_reboot=False)
