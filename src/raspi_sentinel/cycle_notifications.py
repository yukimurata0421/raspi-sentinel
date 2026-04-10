from __future__ import annotations

from pathlib import Path
from typing import Any

from .checks import CheckResult
from .notify import (
    DiscordNotifier,
    collect_system_snapshot,
    format_failures,
    mark_heartbeat_sent,
    should_send_periodic_heartbeat,
)
from .state_helpers import safe_int, target_state
from .status_events import record_notify_failure_event


def schedule_followup(
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

    if action in ("restart", "reboot"):
        followups[target_name] = event


def send_issue_notification(
    notifier: DiscordNotifier,
    target_name: str,
    result: CheckResult,
    action: str,
    consecutive_failures: int,
    services: list[str],
    dry_run: bool,
    events_file: Path | None = None,
    events_max_bytes: int = 0,
    now_ts: float | None = None,
) -> None:
    severity = "ERROR" if action == "reboot" else "WARN"
    sent = notifier.send_lines(
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
    if not sent and events_file is not None and now_ts is not None:
        record_notify_failure_event(
            events_file,
            events_max_bytes,
            f"issue_notification:{target_name}",
            now_ts,
        )


def send_recovery_notification(
    notifier: DiscordNotifier,
    target_name: str,
    previous_failures: int,
    events_file: Path | None = None,
    events_max_bytes: int = 0,
    now_ts: float | None = None,
) -> None:
    sent = notifier.send_lines(
        title=f"Recovered: {target_name}",
        severity="INFO",
        lines=[
            "status=healthy",
            f"previous_consecutive_failures={previous_failures}",
            "action_taken=none",
        ],
    )
    if not sent and events_file is not None and now_ts is not None:
        record_notify_failure_event(
            events_file,
            events_max_bytes,
            f"recovery_notification:{target_name}",
            now_ts,
        )


def send_due_followups(
    notifier: DiscordNotifier,
    state: dict[str, Any],
    target_results: dict[str, CheckResult],
    now_ts: float,
    events_file: Path | None = None,
    events_max_bytes: int = 0,
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
        state_for_target = target_state(state, target_name)
        consecutive = safe_int(state_for_target.get("consecutive_failures"), 0)

        if result is None:
            healthy = consecutive == 0
            current_problem = str(state_for_target.get("last_failure_reason", "unknown"))
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
        elif events_file is not None:
            record_notify_failure_event(
                events_file,
                events_max_bytes,
                f"followup:{target_name}",
                now_ts,
            )

    for target_name in to_delete:
        followups.pop(target_name, None)


def send_periodic_heartbeat(
    notifier: DiscordNotifier,
    state: dict[str, Any],
    target_results: dict[str, CheckResult],
    now_ts: float,
    events_file: Path | None = None,
    events_max_bytes: int = 0,
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
    elif events_file is not None:
        record_notify_failure_event(
            events_file,
            events_max_bytes,
            "periodic_heartbeat",
            now_ts,
        )
