from __future__ import annotations

import argparse
import logging
from pathlib import Path
import subprocess
import time
from typing import Any

from .checks import CheckResult, run_checks
from .config import AppConfig, load_config
from .logging_utils import configure_logging
from .notify import (
    DiscordNotifier,
    collect_system_snapshot,
    format_failures,
    mark_heartbeat_sent,
    should_send_periodic_heartbeat,
)
from .recovery import apply_recovery
from .state import StateStore

LOG = logging.getLogger(__name__)


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _target_state(state: dict[str, Any], target_name: str) -> dict[str, Any]:
    targets = state.setdefault("targets", {})
    target_state = targets.get(target_name)
    if not isinstance(target_state, dict):
        target_state = {}
        targets[target_name] = target_state
    return target_state


def _run_shell_success(command: str, timeout_sec: int) -> bool:
    try:
        result = subprocess.run(
            command,
            shell=True,
            check=False,
            timeout=timeout_sec,
            capture_output=True,
            text=True,
        )
    except (subprocess.TimeoutExpired, OSError):
        return False
    return result.returncode == 0


def _is_target_suppressed_by_maintenance(
    target: Any,
    target_state: dict[str, Any],
    now_ts: float,
) -> tuple[bool, str]:
    suppress_until_raw = target_state.get("maintenance_suppress_until_ts", 0)
    try:
        suppress_until = float(suppress_until_raw)
    except (TypeError, ValueError):
        suppress_until = 0.0

    if now_ts < suppress_until:
        remain = int(suppress_until - now_ts)
        return True, f"grace active ({remain}s remaining)"

    command = target.maintenance_mode_command
    if not command:
        return False, ""

    timeout = target.maintenance_mode_timeout_sec or 10
    matched = _run_shell_success(command=command, timeout_sec=timeout)
    if not matched:
        return False, ""

    grace_sec = target.maintenance_grace_sec or 0
    if grace_sec > 0:
        target_state["maintenance_suppress_until_ts"] = now_ts + grace_sec
    return True, "maintenance mode command matched"


def _schedule_followup(
    state: dict[str, Any],
    target_name: str,
    now_ts: float,
    delay_sec: int,
    action: str,
    reason: str,
    consecutive_failures: int,
) -> None:
    followups = state.setdefault("followups", {})
    existing = followups.get(target_name)
    event = {
        "due_ts": now_ts + delay_sec,
        "created_ts": now_ts,
        "initial_action": action,
        "initial_reason": reason,
        "initial_consecutive_failures": consecutive_failures,
    }

    if not isinstance(existing, dict):
        followups[target_name] = event
        return

    # Re-schedule when action escalates to stronger recovery.
    if action in ("restart", "reboot"):
        followups[target_name] = event


def _send_issue_notification(
    notifier: DiscordNotifier,
    target_name: str,
    result: CheckResult,
    action: str,
    consecutive_failures: int,
    services: list[str],
    dry_run: bool,
) -> None:
    severity = "ERROR" if action == "reboot" else "WARN"
    notifier.send_lines(
        title=f"Issue detected: {target_name}",
        severity=severity,
        lines=[
            f"problem={format_failures(result)}",
            f"action_taken={action}",
            f"consecutive_failures={consecutive_failures}",
            f"services={', '.join(services) if services else '(none)'}",
            f"dry_run={dry_run}",
            "follow_up=scheduled",
        ],
    )


def _send_recovery_notification(
    notifier: DiscordNotifier,
    target_name: str,
    previous_failures: int,
) -> None:
    notifier.send_lines(
        title=f"Recovered: {target_name}",
        severity="INFO",
        lines=[
            f"status=healthy",
            f"previous_consecutive_failures={previous_failures}",
            "action_taken=none",
        ],
    )


def _send_due_followups(
    notifier: DiscordNotifier,
    state: dict[str, Any],
    target_results: dict[str, CheckResult],
    now_ts: float,
) -> None:
    followups = state.setdefault("followups", {})
    if not isinstance(followups, dict):
        state["followups"] = {}
        return

    to_delete: list[str] = []
    for target_name, event in followups.items():
        if not isinstance(event, dict):
            to_delete.append(target_name)
            continue

        due_ts = event.get("due_ts")
        try:
            due_ts_f = float(due_ts)
        except (TypeError, ValueError):
            due_ts_f = 0.0

        if now_ts < due_ts_f:
            continue

        result = target_results.get(target_name)
        target_state = _target_state(state, target_name)
        consecutive = _safe_int(target_state.get("consecutive_failures"), 0)

        if result is None:
            healthy = consecutive == 0
            current_problem = str(target_state.get("last_failure_reason", "unknown"))
        else:
            healthy = result.healthy
            current_problem = format_failures(result)

        severity = "INFO" if healthy else "WARN"
        sent = notifier.send_lines(
            title=f"Follow-up after 5min: {target_name}",
            severity=severity,
            lines=[
                f"initial_action={event.get('initial_action', 'unknown')}",
                f"initial_problem={event.get('initial_reason', 'unknown')}",
                f"current_status={'healthy' if healthy else 'unhealthy'}",
                f"current_problem={current_problem}",
                f"current_consecutive_failures={consecutive}",
            ],
        )
        if sent:
            to_delete.append(target_name)

    for target_name in to_delete:
        followups.pop(target_name, None)


def _send_periodic_heartbeat(
    notifier: DiscordNotifier,
    state: dict[str, Any],
    target_results: dict[str, CheckResult],
    now_ts: float,
) -> None:
    interval_sec = notifier.config.heartbeat_interval_sec
    if interval_sec <= 0:
        return
    if not should_send_periodic_heartbeat(state=state, interval_sec=interval_sec, now_ts=now_ts):
        return

    healthy_count = sum(1 for result in target_results.values() if result.healthy)
    unhealthy_count = sum(1 for result in target_results.values() if not result.healthy)
    snapshot = collect_system_snapshot()
    pending_followups = state.get("followups", {})
    pending_count = len(pending_followups) if isinstance(pending_followups, dict) else 0

    sent = notifier.send_lines(
        title="Heartbeat: monitor running",
        severity="INFO",
        lines=[
            f"targets_healthy={healthy_count}",
            f"targets_unhealthy={unhealthy_count}",
            f"uptime_sec={snapshot.uptime_sec:.0f}",
            f"loadavg={snapshot.load1:.2f}/{snapshot.load5:.2f}/{snapshot.load15:.2f}",
            f"root_disk_used_pct={snapshot.disk_used_pct:.1f}",
            f"pending_followups={pending_count}",
        ],
    )
    if sent:
        mark_heartbeat_sent(state=state, now_ts=now_ts)


def _run_cycle(config: AppConfig, dry_run: bool) -> int:
    store = StateStore(config.global_config.state_file)
    state = store.load()
    notifier = DiscordNotifier(config.notify_config.discord)

    now_ts = time.time()
    unhealthy_count = 0
    reboot_requested = False
    target_results: dict[str, CheckResult] = {}

    for target in config.targets:
        before_state = _target_state(state, target.name)
        previous_failures = _safe_int(before_state.get("consecutive_failures"), 0)

        suppressed, suppress_reason = _is_target_suppressed_by_maintenance(
            target=target,
            target_state=before_state,
            now_ts=now_ts,
        )
        if suppressed:
            LOG.info(
                "target '%s' checks suppressed by maintenance mode: %s",
                target.name,
                suppress_reason,
            )
            continue

        result = run_checks(target)
        target_results[target.name] = result

        if not result.healthy:
            unhealthy_count += 1

        outcome = apply_recovery(
            target=target,
            check_result=result,
            global_config=config.global_config,
            state=state,
            dry_run=dry_run,
        )

        after_state = _target_state(state, target.name)
        current_failures = _safe_int(after_state.get("consecutive_failures"), 0)

        if notifier.enabled:
            if result.healthy and previous_failures > 0:
                _send_recovery_notification(
                    notifier=notifier,
                    target_name=target.name,
                    previous_failures=previous_failures,
                )

            if not result.healthy:
                should_notify_now = current_failures == 1 or outcome.action in ("restart", "reboot")
                if should_notify_now:
                    _send_issue_notification(
                        notifier=notifier,
                        target_name=target.name,
                        result=result,
                        action=outcome.action,
                        consecutive_failures=current_failures,
                        services=target.services,
                        dry_run=dry_run,
                    )

                _schedule_followup(
                    state=state,
                    target_name=target.name,
                    now_ts=now_ts,
                    delay_sec=notifier.config.followup_delay_sec,
                    action=outcome.action,
                    reason=format_failures(result),
                    consecutive_failures=current_failures,
                )

        if outcome.requested_reboot:
            reboot_requested = True
            LOG.error("reboot requested after evaluating target '%s'", target.name)
            break

    if notifier.enabled:
        _send_due_followups(
            notifier=notifier,
            state=state,
            target_results=target_results,
            now_ts=now_ts,
        )
        _send_periodic_heartbeat(
            notifier=notifier,
            state=state,
            target_results=target_results,
            now_ts=now_ts,
        )

    store.save(state)

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
