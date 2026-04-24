from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .checks import CheckResult
from .notify import (
    DiscordNotifier,
    collect_system_snapshot,
    format_failures,
    mark_heartbeat_sent,
    should_send_periodic_heartbeat,
)
from .state_helpers import safe_int
from .state_models import FollowupRecord, GlobalState, NotifyDeliveryBacklog
from .status_events import record_notify_failure_event

MAX_NOTIFY_BACKLOG_CONTEXTS = 64
_OVERFLOW_CONTEXT = "__other__"


@dataclass(slots=True)
class NotificationSendResult:
    sent: bool
    network_failed: bool


@dataclass(slots=True)
class DeliveryBacklogManager:
    state: GlobalState
    retry_interval_sec: int

    def record_network_failure(self, *, context: str, now_ts: float) -> None:
        notify_state = self.state.notify
        backlog = notify_state.delivery_backlog
        if backlog is None:
            backlog = NotifyDeliveryBacklog(
                first_failed_ts=now_ts,
                last_failed_ts=now_ts,
                total_failures=1,
                contexts={context: 1},
            )
            notify_state.delivery_backlog = backlog
        else:
            backlog.last_failed_ts = max(backlog.last_failed_ts, now_ts)
            backlog.total_failures += 1
            if context in backlog.contexts:
                backlog.contexts[context] += 1
            elif len(backlog.contexts) >= MAX_NOTIFY_BACKLOG_CONTEXTS:
                backlog.contexts[_OVERFLOW_CONTEXT] = backlog.contexts.get(_OVERFLOW_CONTEXT, 0) + 1
            else:
                backlog.contexts[context] = 1

        if notify_state.retry_due_ts is None or notify_state.retry_due_ts <= now_ts:
            notify_state.retry_due_ts = now_ts + self.retry_interval_sec

    def should_send_summary(self, *, now_ts: float) -> bool:
        backlog = self.state.notify.delivery_backlog
        if backlog is None:
            return False
        retry_due_ts = self.state.notify.retry_due_ts
        return retry_due_ts is None or now_ts >= retry_due_ts

    def mark_summary_sent(self) -> None:
        self.state.notify.delivery_backlog = None
        self.state.notify.retry_due_ts = None

    def mark_summary_network_failure(self, *, now_ts: float) -> None:
        backlog = self.state.notify.delivery_backlog
        if backlog is not None:
            backlog.last_failed_ts = max(backlog.last_failed_ts, now_ts)
        self.state.notify.retry_due_ts = now_ts + self.retry_interval_sec

    def defer_summary_retry(self, *, now_ts: float) -> None:
        self.state.notify.retry_due_ts = now_ts + self.retry_interval_sec


def _iso_ts(ts: float) -> str:
    return datetime.fromtimestamp(ts).astimezone().isoformat(timespec="seconds")


def _send_with_tracking(
    notifier: DiscordNotifier,
    *,
    title: str,
    severity: str,
    lines: list[str],
    context: str,
    state: GlobalState | None,
    retry_interval_sec: int,
    events_file: Path | None,
    events_max_bytes: int,
    events_backup_generations: int,
    now_ts: float | None,
) -> NotificationSendResult:
    sent = notifier.send_lines(
        title=title,
        severity=severity,
        lines=lines,
    )
    network_failed = (not sent) and notifier.last_failure_kind == "network"
    if not sent and events_file is not None and now_ts is not None:
        record_notify_failure_event(
            events_file,
            events_max_bytes,
            events_backup_generations,
            context,
            now_ts,
        )
    if network_failed and state is not None and now_ts is not None:
        manager = DeliveryBacklogManager(state=state, retry_interval_sec=retry_interval_sec)
        manager.record_network_failure(context=context, now_ts=now_ts)
    return NotificationSendResult(sent=sent, network_failed=network_failed)


def schedule_followup(
    state: GlobalState,
    target_name: str,
    now_ts: float,
    delay_sec: int,
    action: str,
    reason: str,
    consecutive_failures: int,
) -> None:
    existing = state.followups.get(target_name)
    event = FollowupRecord(
        due_ts=now_ts + delay_sec,
        created_ts=now_ts,
        initial_action=action,
        initial_reason=reason,
        initial_consecutive_failures=consecutive_failures,
    )

    if existing is None:
        state.followups[target_name] = event
        return

    if action in ("restart", "reboot"):
        state.followups[target_name] = event


def send_issue_notification(
    notifier: DiscordNotifier,
    state: GlobalState | None,
    target_name: str,
    result: CheckResult,
    action: str,
    consecutive_failures: int,
    services: list[str],
    dry_run: bool,
    events_file: Path | None = None,
    events_max_bytes: int = 0,
    events_backup_generations: int = 1,
    now_ts: float | None = None,
) -> NotificationSendResult:
    severity = "ERROR" if action == "reboot" else "WARN"
    return _send_with_tracking(
        notifier=notifier,
        title=f"Issue detected: {target_name}",
        severity=severity,
        context=f"issue_notification:{target_name}",
        state=state,
        retry_interval_sec=notifier.config.retry_interval_sec,
        events_file=events_file,
        events_max_bytes=events_max_bytes,
        events_backup_generations=events_backup_generations,
        now_ts=now_ts,
        lines=[
            f"problem={format_failures(result)}",
            f"action_taken={action}",
            f"consecutive_failures={consecutive_failures}",
            f"services={', '.join(services) if services else '(none)'}",
            f"dry_run={dry_run}",
            "follow_up=scheduled",
        ],
    )


def send_recovery_notification(
    notifier: DiscordNotifier,
    state: GlobalState | None,
    target_name: str,
    previous_failures: int,
    events_file: Path | None = None,
    events_max_bytes: int = 0,
    events_backup_generations: int = 1,
    now_ts: float | None = None,
) -> NotificationSendResult:
    return _send_with_tracking(
        notifier=notifier,
        title=f"Recovered: {target_name}",
        severity="INFO",
        context=f"recovery_notification:{target_name}",
        state=state,
        retry_interval_sec=notifier.config.retry_interval_sec,
        events_file=events_file,
        events_max_bytes=events_max_bytes,
        events_backup_generations=events_backup_generations,
        now_ts=now_ts,
        lines=[
            "status=healthy",
            f"previous_consecutive_failures={previous_failures}",
            "action_taken=none",
        ],
    )


def send_due_followups(
    notifier: DiscordNotifier,
    state: GlobalState,
    target_results: dict[str, CheckResult],
    now_ts: float,
    events_file: Path | None = None,
    events_max_bytes: int = 0,
    events_backup_generations: int = 1,
) -> None:
    to_delete: list[str] = []
    for target_name, event in state.followups.items():
        due_ts_f = event.due_ts

        if now_ts < due_ts_f:
            continue

        result = target_results.get(target_name)
        state_for_target = state.ensure_target(target_name)
        consecutive = safe_int(state_for_target.consecutive_failures, 0)

        if result is None:
            healthy = consecutive == 0
            current_problem = state_for_target.last_failure_reason or "unknown"
        else:
            healthy = result.healthy
            current_problem = format_failures(result)

        severity = "INFO" if healthy else "WARN"
        send_result = _send_with_tracking(
            notifier=notifier,
            title=f"Follow-up after 5min: {target_name}",
            severity=severity,
            context=f"followup:{target_name}",
            state=state,
            retry_interval_sec=notifier.config.retry_interval_sec,
            events_file=events_file,
            events_max_bytes=events_max_bytes,
            events_backup_generations=events_backup_generations,
            now_ts=now_ts,
            lines=[
                f"initial_action={event.initial_action}",
                f"initial_problem={event.initial_reason}",
                f"current_status={'healthy' if healthy else 'unhealthy'}",
                f"current_problem={current_problem}",
                f"current_consecutive_failures={consecutive}",
            ],
        )
        if send_result.sent:
            to_delete.append(target_name)

    for target_name in to_delete:
        state.followups.pop(target_name, None)


def send_periodic_heartbeat(
    notifier: DiscordNotifier,
    state: GlobalState,
    target_results: dict[str, CheckResult],
    now_ts: float,
    events_file: Path | None = None,
    events_max_bytes: int = 0,
    events_backup_generations: int = 1,
) -> None:
    interval_sec = notifier.config.heartbeat_interval_sec
    if interval_sec <= 0:
        return
    if not should_send_periodic_heartbeat(state=state, interval_sec=interval_sec, now_ts=now_ts):
        return

    healthy_count = sum(1 for result in target_results.values() if result.healthy)
    unhealthy_count = sum(1 for result in target_results.values() if not result.healthy)
    snapshot = collect_system_snapshot()
    pending_count = len(state.followups)

    send_result = _send_with_tracking(
        notifier=notifier,
        title="Heartbeat: monitor running",
        severity="INFO",
        context="periodic_heartbeat",
        state=state,
        retry_interval_sec=notifier.config.retry_interval_sec,
        events_file=events_file,
        events_max_bytes=events_max_bytes,
        events_backup_generations=events_backup_generations,
        now_ts=now_ts,
        lines=[
            f"targets_healthy={healthy_count}",
            f"targets_unhealthy={unhealthy_count}",
            f"uptime_sec={snapshot.uptime_sec:.0f}",
            f"loadavg={snapshot.load1:.2f}/{snapshot.load5:.2f}/{snapshot.load15:.2f}",
            f"root_disk_used_pct={snapshot.disk_used_pct:.1f}",
            f"pending_followups={pending_count}",
        ],
    )
    if send_result.sent:
        mark_heartbeat_sent(state=state, now_ts=now_ts)


def send_delivery_backlog_summary(
    notifier: DiscordNotifier,
    state: GlobalState,
    now_ts: float,
    events_file: Path | None = None,
    events_max_bytes: int = 0,
    events_backup_generations: int = 1,
) -> None:
    manager = DeliveryBacklogManager(
        state=state, retry_interval_sec=notifier.config.retry_interval_sec
    )
    backlog = state.notify.delivery_backlog
    if backlog is None or not manager.should_send_summary(now_ts=now_ts):
        return

    top_contexts = sorted(
        backlog.contexts.items(),
        key=lambda item: (-item[1], item[0]),
    )
    contexts_summary = ", ".join(f"{context}={count}" for context, count in top_contexts[:5])
    if len(top_contexts) > 5:
        contexts_summary = f"{contexts_summary}, ..."

    lines = [
        f"delivery_failed_from={_iso_ts(backlog.first_failed_ts)}",
        f"delivery_failed_until={_iso_ts(backlog.last_failed_ts)}",
        f"failed_notifications_total={backlog.total_failures}",
        f"contexts={contexts_summary or 'unknown'}",
        f"retry_interval_sec={notifier.config.retry_interval_sec}",
    ]
    sent = notifier.send_lines(
        title="Delayed notifications summary",
        severity="WARN",
        lines=lines,
    )
    if sent:
        manager.mark_summary_sent()
        return

    if events_file is not None:
        record_notify_failure_event(
            events_file,
            events_max_bytes,
            events_backup_generations,
            "deferred_notification_batch",
            now_ts,
        )
    if notifier.last_failure_kind == "network":
        manager.mark_summary_network_failure(now_ts=now_ts)
        return
    manager.defer_summary_retry(now_ts=now_ts)
