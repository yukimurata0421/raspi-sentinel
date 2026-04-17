from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from .checks import CheckResult, apply_records_progress_check, run_checks
from .config import AppConfig, TargetConfig
from .cycle_notifications import (
    schedule_followup,
    send_delivery_backlog_summary,
    send_due_followups,
    send_issue_notification,
    send_periodic_heartbeat,
    send_recovery_notification,
)
from .maintenance import is_target_suppressed_by_maintenance
from .monitor_stats import maybe_write_monitor_stats
from .notify import DiscordNotifier, format_failures
from .policy import PolicySnapshot, classify_target_policy
from .recovery import RecoveryOutcome, apply_recovery
from .state import StateLoadDiagnostics, StateStore
from .state_models import GlobalState
from .status_events import (
    append_event,
    apply_policy_to_result,
    build_event_evidence,
    record_status_events,
)
from .time_health import apply_time_health_checks

LOG = logging.getLogger(__name__)


def evaluate_target(
    target: TargetConfig,
    state: GlobalState,
    now_ts: float,
    now_mono_ts: float | None = None,
) -> tuple[CheckResult, PolicySnapshot] | None:
    before = state.ensure_target(target.name)

    suppressed, suppress_reason = is_target_suppressed_by_maintenance(
        target=target,
        target_state=before,
        now_ts=now_ts,
    )
    if suppressed:
        LOG.info(
            "target '%s' checks suppressed by maintenance mode: %s",
            target.name,
            suppress_reason,
        )
        return None

    result = run_checks(target, now_wall_ts=now_ts)
    apply_records_progress_check(
        target=target,
        target_state=before,
        result=result,
    )
    apply_time_health_checks(
        target=target,
        target_state=before,
        result=result,
        now_wall_ts=now_ts,
        now_mono_ts=now_mono_ts,
    )
    policy = classify_target_policy(result=result, target_state=before)
    apply_policy_to_result(result, policy)
    return result, policy


def apply_recovery_phase(
    target: TargetConfig,
    result: CheckResult,
    config: AppConfig,
    state: GlobalState,
    dry_run: bool,
    now_ts: float,
    allow_disruptive_actions: bool = True,
) -> RecoveryOutcome:
    return apply_recovery(
        target=target,
        check_result=result,
        global_config=config.global_config,
        state=state,
        dry_run=dry_run,
        allow_disruptive_actions=allow_disruptive_actions,
        now_ts=now_ts,
    )


def emit_target_notifications(
    notifier: DiscordNotifier,
    state: GlobalState,
    target: TargetConfig,
    result: CheckResult,
    outcome: RecoveryOutcome,
    previous_failures: int,
    current_failures: int,
    dry_run: bool,
    events_file: Path,
    events_max_bytes: int,
    events_backup_generations: int,
    now_ts: float,
) -> None:
    if not notifier.enabled:
        return

    if notifier.config.notify_on_recovery and result.healthy and previous_failures > 0:
        send_recovery_notification(
            notifier=notifier,
            state=state,
            target_name=target.name,
            previous_failures=previous_failures,
            events_file=events_file,
            events_max_bytes=events_max_bytes,
            events_backup_generations=events_backup_generations,
            now_ts=now_ts,
        )

    if not result.healthy:
        should_notify_now = current_failures == 1 or outcome.action in (
            "restart",
            "reboot",
        )
        if should_notify_now:
            send_issue_notification(
                notifier=notifier,
                state=state,
                target_name=target.name,
                result=result,
                action=outcome.action,
                consecutive_failures=current_failures,
                services=target.services,
                dry_run=dry_run,
                events_file=events_file,
                events_max_bytes=events_max_bytes,
                events_backup_generations=events_backup_generations,
                now_ts=now_ts,
            )

        schedule_followup(
            state=state,
            target_name=target.name,
            now_ts=now_ts,
            delay_sec=notifier.config.followup_delay_sec,
            action=outcome.action,
            reason=format_failures(result),
            consecutive_failures=current_failures,
        )


def persist_cycle_outputs(
    store: StateStore,
    state: GlobalState,
    max_file_bytes: int,
    max_reboots_entries: int,
) -> bool:
    return store.save(
        state,
        max_file_bytes=max_file_bytes,
        max_reboots_entries=max_reboots_entries,
    )


def _overall_status(target_reports: dict[str, dict[str, Any]]) -> str:
    has_failed = any(report.get("status") == "failed" for report in target_reports.values())
    if has_failed:
        return "failed"
    has_degraded = any(report.get("status") == "degraded" for report in target_reports.values())
    if has_degraded:
        return "degraded"
    return "ok"


def _record_state_load_issue_event(
    diagnostics: StateLoadDiagnostics,
    events_file: Path,
    max_file_bytes: int,
    backup_generations: int,
    now_ts: float,
) -> None:
    if not diagnostics.limited_mode:
        return

    ts_text = datetime.fromtimestamp(now_ts).astimezone().isoformat(timespec="seconds")
    kind = "state_corrupted" if diagnostics.state_corrupted else "state_load_error"
    event: dict[str, Any] = {
        "ts": ts_text,
        "kind": kind,
        "reason": diagnostics.state_load_error or kind,
    }
    if diagnostics.corrupt_backup_path is not None:
        event["backup_path"] = str(diagnostics.corrupt_backup_path)
    append_event(
        events_file=events_file,
        event=event,
        max_file_bytes=max_file_bytes,
        backup_generations=backup_generations,
    )


def _run_cycle_collect_locked(
    config: AppConfig,
    dry_run: bool,
    store: StateStore,
    now_ts: float,
    mono_provider: Callable[[], float],
) -> tuple[int, dict[str, Any]]:
    state, state_diagnostics = store.load_with_diagnostics()
    limited_mode = state_diagnostics.limited_mode
    notifier = DiscordNotifier(config.notify_config.discord)

    unhealthy_count = 0
    reboot_requested = False
    target_results: dict[str, CheckResult] = {}
    target_reports: dict[str, dict[str, Any]] = {}
    events_file = config.global_config.events_file
    events_max = config.global_config.events_max_file_bytes
    events_backups = config.global_config.events_backup_generations

    _record_state_load_issue_event(
        diagnostics=state_diagnostics,
        events_file=events_file,
        max_file_bytes=events_max,
        backup_generations=events_backups,
        now_ts=now_ts,
    )

    for target in config.targets:
        before = state.ensure_target(target.name)
        previous_failures = before.consecutive_failures

        evaluated = evaluate_target(
            target=target,
            state=state,
            now_ts=now_ts,
            now_mono_ts=mono_provider(),
        )
        if evaluated is None:
            target_reports[target.name] = {
                "status": "ok",
                "reason": "maintenance_suppressed",
                "action": "none",
                "healthy": True,
                "evidence": {},
            }
            continue

        result, policy = evaluated
        target_results[target.name] = result

        if policy.status != "ok":
            unhealthy_count += 1

        outcome = apply_recovery_phase(
            target=target,
            result=result,
            config=config,
            state=state,
            dry_run=dry_run,
            now_ts=now_ts,
            allow_disruptive_actions=not limited_mode,
        )

        after = state.ensure_target(target.name)
        current_failures = after.consecutive_failures
        record_status_events(
            events_file=events_file,
            target_state=after,
            target_name=target.name,
            current_status=policy.status,
            current_reason=policy.reason,
            result=result,
            action=outcome.action,
            now_ts=now_ts,
            max_file_bytes=events_max,
            backup_generations=events_backups,
            current_subreason=policy.subreason,
        )

        emit_target_notifications(
            notifier=notifier,
            state=state,
            target=target,
            result=result,
            outcome=outcome,
            previous_failures=previous_failures,
            current_failures=current_failures,
            dry_run=dry_run,
            events_file=events_file,
            events_max_bytes=events_max,
            events_backup_generations=events_backups,
            now_ts=now_ts,
        )

        report_payload: dict[str, Any] = {
            "status": policy.status,
            "reason": policy.reason,
            "action": outcome.action,
            "healthy": result.healthy,
            "evidence": build_event_evidence(result),
        }
        if policy.subreason is not None:
            report_payload["subreason"] = policy.subreason
        if result.failures:
            report_payload["failures"] = [
                {"check": failure.check, "message": failure.message} for failure in result.failures
            ]
        target_reports[target.name] = report_payload

        if outcome.requested_reboot:
            reboot_requested = True
            LOG.error("reboot requested after evaluating target '%s'", target.name)
            break

    if notifier.enabled:
        send_due_followups(
            notifier=notifier,
            state=state,
            target_results=target_results,
            now_ts=now_ts,
            events_file=events_file,
            events_max_bytes=events_max,
            events_backup_generations=events_backups,
        )
        send_periodic_heartbeat(
            notifier=notifier,
            state=state,
            target_results=target_results,
            now_ts=now_ts,
            events_file=events_file,
            events_max_bytes=events_max,
            events_backup_generations=events_backups,
        )
        send_delivery_backlog_summary(
            notifier=notifier,
            state=state,
            now_ts=now_ts,
            events_file=events_file,
            events_max_bytes=events_max,
            events_backup_generations=events_backups,
        )

    maybe_write_monitor_stats(
        config=config,
        state=state,
        target_results=target_results,
        now_ts=now_ts,
    )

    updated_at = datetime.fromtimestamp(now_ts).astimezone().isoformat(timespec="seconds")
    overall_status = _overall_status(target_reports)
    if limited_mode and overall_status == "ok":
        overall_status = "degraded"
    report: dict[str, Any] = {
        "updated_at": updated_at,
        "overall_status": overall_status,
        "dry_run": dry_run,
        "reboot_requested": reboot_requested,
        "targets": target_reports,
        "limited_mode": limited_mode,
        "state_persisted": False,
    }
    if state_diagnostics.state_load_error:
        report["state_issue"] = state_diagnostics.state_load_error
    if state_diagnostics.corrupt_backup_path is not None:
        report["state_corrupt_backup_path"] = str(state_diagnostics.corrupt_backup_path)

    persisted = persist_cycle_outputs(
        store=store,
        state=state,
        max_file_bytes=config.global_config.state_max_file_bytes,
        max_reboots_entries=config.global_config.state_reboots_max_entries,
    )
    report["state_persisted"] = persisted
    if not persisted:
        LOG.error("cycle state persistence failed")
        return 14, report

    if reboot_requested:
        return 2, report
    if unhealthy_count or limited_mode:
        return 1, report
    return 0, report


def run_cycle_collect(
    config: AppConfig,
    dry_run: bool,
    time_provider: Callable[[], float],
    mono_provider: Callable[[], float],
) -> tuple[int, dict[str, Any]]:
    store = StateStore(config.global_config.state_file)
    try:
        with store.exclusive_lock(timeout_sec=config.global_config.state_lock_timeout_sec):
            now_ts = time_provider()
            return _run_cycle_collect_locked(
                config=config,
                dry_run=dry_run,
                store=store,
                now_ts=now_ts,
                mono_provider=mono_provider,
            )
    except (TimeoutError, OSError) as exc:
        LOG.error("%s", exc)
        now_ts = time_provider()
        updated_at = datetime.fromtimestamp(now_ts).astimezone().isoformat(timespec="seconds")
        reason = "state_lock_timeout" if isinstance(exc, TimeoutError) else "state_lock_error"
        report = {
            "updated_at": updated_at,
            "overall_status": "failed",
            "dry_run": dry_run,
            "reboot_requested": False,
            "targets": {},
            "reason": reason,
            "state_persisted": False,
            "limited_mode": False,
        }
        return 13, report
