from __future__ import annotations

import argparse
import logging
from pathlib import Path
import time
from typing import Any

from .checks import CheckResult, run_checks
from .config import AppConfig, TargetConfig, load_config
from .policy import PolicySnapshot, classify_target_policy
from .cycle_notifications import (
    schedule_followup,
    send_due_followups,
    send_issue_notification,
    send_periodic_heartbeat,
    send_recovery_notification,
)
from .logging_utils import configure_logging
from .maintenance import is_target_suppressed_by_maintenance
from .monitor_stats import apply_records_progress_check, maybe_write_monitor_stats
from .notify import DiscordNotifier, format_failures
from .recovery import RecoveryOutcome, apply_recovery
from .runtime_state import safe_int, target_state
from .state import StateStore
from .state_models import TargetState as TargetStateView
from .status_events import (
    apply_policy_to_result,
    classify_target_reason,
    classify_target_state,
    classify_target_status,
    record_status_events,
)
from .time_health import apply_time_health_checks

LOG = logging.getLogger(__name__)


def _classify_target_status(result: CheckResult) -> str:
    return classify_target_status(result)


def _classify_target_reason(result: CheckResult) -> str:
    return classify_target_reason(result)


def evaluate_target(
    target: TargetConfig,
    state: dict[str, Any],
    now_ts: float,
) -> tuple[CheckResult, PolicySnapshot] | None:
    """Run checks, semantic progress, time health; return policy snapshot or None if suppressed."""
    before = target_state(state, target.name)

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
    )
    policy = classify_target_policy(result=result, target_state=before)
    apply_policy_to_result(result, policy)
    return result, policy


def apply_recovery_phase(
    target: TargetConfig,
    result: CheckResult,
    global_config: AppConfig,
    state: dict[str, Any],
    dry_run: bool,
    now_ts: float,
) -> RecoveryOutcome:
    return apply_recovery(
        target=target,
        check_result=result,
        global_config=global_config.global_config,
        state=state,
        dry_run=dry_run,
        now_ts=now_ts,
    )


def emit_target_notifications(
    notifier: DiscordNotifier,
    state: dict[str, Any],
    target: TargetConfig,
    result: CheckResult,
    outcome: RecoveryOutcome,
    previous_failures: int,
    current_failures: int,
    dry_run: bool,
    events_file: Path,
    events_max_bytes: int,
    now_ts: float,
) -> None:
    if not notifier.enabled:
        return

    if result.healthy and previous_failures > 0:
        send_recovery_notification(
            notifier=notifier,
            target_name=target.name,
            previous_failures=previous_failures,
            events_file=events_file,
            events_max_bytes=events_max_bytes,
            now_ts=now_ts,
        )

    if not result.healthy:
        should_notify_now = current_failures == 1 or outcome.action in ("restart", "reboot")
        if should_notify_now:
            send_issue_notification(
                notifier=notifier,
                target_name=target.name,
                result=result,
                action=outcome.action,
                consecutive_failures=current_failures,
                services=target.services,
                dry_run=dry_run,
                events_file=events_file,
                events_max_bytes=events_max_bytes,
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
    state: dict[str, Any],
) -> None:
    store.save(state)


def _run_cycle(config: AppConfig, dry_run: bool) -> int:
    store = StateStore(config.global_config.state_file)
    state = store.load()
    notifier = DiscordNotifier(config.notify_config.discord)

    now_ts = time.time()
    unhealthy_count = 0
    reboot_requested = False
    target_results: dict[str, CheckResult] = {}
    events_file = config.global_config.events_file
    events_max = config.global_config.events_max_file_bytes

    for target in config.targets:
        evaluated = evaluate_target(target=target, state=state, now_ts=now_ts)
        if evaluated is None:
            continue

        result, policy = evaluated
        target_results[target.name] = result

        if policy.status != "ok":
            unhealthy_count += 1

        before = target_state(state, target.name)
        previous_failures = TargetStateView.from_dict(before).consecutive_failures

        outcome = apply_recovery_phase(
            target=target,
            result=result,
            global_config=config,
            state=state,
            dry_run=dry_run,
            now_ts=now_ts,
        )

        after = target_state(state, target.name)
        current_failures = safe_int(after.get("consecutive_failures"), 0)
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
            now_ts=now_ts,
        )

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
        )
        send_periodic_heartbeat(
            notifier=notifier,
            state=state,
            target_results=target_results,
            now_ts=now_ts,
            events_file=events_file,
            events_max_bytes=events_max,
        )

    maybe_write_monitor_stats(
        config=config,
        state=state,
        target_results=target_results,
        now_ts=now_ts,
    )

    persist_cycle_outputs(store=store, state=state)

    if reboot_requested:
        return 2
    if unhealthy_count:
        return 1
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="raspi-sentinel: staged logical self-healing recovery for Raspberry Pi"
    )
    parser.add_argument(
        "-c",
        "--config",
        type=Path,
        default=Path("/etc/raspi-sentinel/config.toml"),
        help="Path to TOML config file",
    )
    parser.add_argument("--dry-run", action="store_true", help="Evaluate and log actions but do not restart/reboot")
    parser.add_argument("--verbose", action="store_true", help="Enable debug logging")

    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("run-once", help="Run one health/recovery evaluation cycle")

    loop_parser = sub.add_parser("loop", help="Run health checks in a continuous loop")
    loop_parser.add_argument(
        "--interval-sec",
        type=int,
        default=None,
        help="Override loop interval (default comes from [global].loop_interval_sec)",
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    configure_logging(verbose=args.verbose)

    try:
        config = load_config(args.config)
    except Exception as exc:
        LOG.error("failed to load config '%s': %s", args.config, exc)
        return 10

    if args.command == "run-once":
        rc = _run_cycle(config=config, dry_run=args.dry_run)
        return 0 if rc in (0, 1, 2) else rc

    if args.command == "loop":
        interval = args.interval_sec or config.global_config.loop_interval_sec
        if interval <= 0:
            LOG.error("loop interval must be > 0")
            return 11

        LOG.info("starting loop mode interval=%ss", interval)
        while True:
            rc = _run_cycle(config=config, dry_run=args.dry_run)
            if rc == 2:
                LOG.error("reboot was requested; exiting loop")
                return 0
            time.sleep(interval)

    parser.print_help()
    return 12
