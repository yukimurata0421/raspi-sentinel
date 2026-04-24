from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

from conftest import make_discord_config

from raspi_sentinel.checks import CheckFailure, CheckResult
from raspi_sentinel.cycle_notifications import (
    MAX_NOTIFY_BACKLOG_CONTEXTS,
    DeliveryBacklogManager,
    schedule_followup,
    send_delivery_backlog_summary,
    send_due_followups,
    send_issue_notification,
    send_periodic_heartbeat,
    send_recovery_notification,
)
from raspi_sentinel.notify import DiscordNotifier
from raspi_sentinel.state_models import FollowupRecord, GlobalState, NotifyDeliveryBacklog


def _notifier(enabled: bool = True, **overrides: Any) -> DiscordNotifier:
    return DiscordNotifier(
        make_discord_config(
            enabled=enabled,
            webhook_url="https://discord.com/api/webhooks/test/token" if enabled else None,
            **overrides,
        )
    )


def _result(
    healthy: bool = True,
    failures: list[CheckFailure] | None = None,
    observations: dict[str, Any] | None = None,
) -> CheckResult:
    r = CheckResult(
        target="demo",
        healthy=healthy,
        failures=failures or [],
    )
    if observations:
        r.observations.update(observations)
    return r


class TestScheduleFollowup:
    def test_creates_new_followup(self) -> None:
        state = GlobalState()
        schedule_followup(
            state=state,
            target_name="svc",
            now_ts=1000.0,
            delay_sec=300,
            action="restart",
            reason="service down",
            consecutive_failures=3,
        )
        assert "svc" in state.followups
        followup = state.followups["svc"]
        assert followup.due_ts == 1300.0
        assert followup.initial_action == "restart"
        assert followup.initial_consecutive_failures == 3

    def test_escalation_replaces_existing(self) -> None:
        state = GlobalState()
        state.followups["svc"] = FollowupRecord(
            due_ts=1100.0,
            created_ts=800.0,
            initial_action="warn",
            initial_reason="degraded",
            initial_consecutive_failures=1,
        )
        schedule_followup(
            state=state,
            target_name="svc",
            now_ts=1000.0,
            delay_sec=300,
            action="restart",
            reason="service down",
            consecutive_failures=5,
        )
        assert state.followups["svc"].initial_action == "restart"

    def test_warn_does_not_replace_existing(self) -> None:
        state = GlobalState()
        state.followups["svc"] = FollowupRecord(
            due_ts=1100.0,
            created_ts=800.0,
            initial_action="restart",
            initial_reason="service down",
            initial_consecutive_failures=3,
        )
        schedule_followup(
            state=state,
            target_name="svc",
            now_ts=1000.0,
            delay_sec=300,
            action="warn",
            reason="still down",
            consecutive_failures=4,
        )
        assert state.followups["svc"].initial_action == "restart"


class TestSendIssueNotification:
    @patch.object(DiscordNotifier, "send_lines", return_value=True)
    def test_sends_notification(self, mock_send: MagicMock, tmp_path: Path) -> None:
        n = _notifier()
        events_file = tmp_path / "events.jsonl"
        send_issue_notification(
            notifier=n,
            state=GlobalState(),
            target_name="svc",
            result=_result(healthy=False, failures=[CheckFailure("cmd", "exit 1")]),
            action="restart",
            consecutive_failures=3,
            services=["svc.service"],
            dry_run=False,
            events_file=events_file,
            events_max_bytes=5_000_000,
            events_backup_generations=1,
            now_ts=1000.0,
        )
        mock_send.assert_called_once()
        call_kwargs = mock_send.call_args
        assert call_kwargs[1]["severity"] == "WARN"

    @patch.object(DiscordNotifier, "send_lines", return_value=True)
    def test_reboot_severity_is_error(self, mock_send: MagicMock, tmp_path: Path) -> None:
        n = _notifier()
        send_issue_notification(
            notifier=n,
            state=GlobalState(),
            target_name="svc",
            result=_result(healthy=False, failures=[CheckFailure("cmd", "exit 1")]),
            action="reboot",
            consecutive_failures=6,
            services=["svc.service"],
            dry_run=False,
            events_file=tmp_path / "events.jsonl",
            now_ts=1000.0,
        )
        assert mock_send.call_args[1]["severity"] == "ERROR"

    @patch.object(DiscordNotifier, "send_lines", return_value=False)
    def test_records_failure_event_on_send_failure(
        self, mock_send: MagicMock, tmp_path: Path
    ) -> None:
        n = _notifier()
        events_file = tmp_path / "events.jsonl"
        send_issue_notification(
            notifier=n,
            state=GlobalState(),
            target_name="svc",
            result=_result(healthy=False, failures=[CheckFailure("cmd", "exit 1")]),
            action="restart",
            consecutive_failures=3,
            services=[],
            dry_run=False,
            events_file=events_file,
            events_max_bytes=5_000_000,
            events_backup_generations=1,
            now_ts=1000.0,
        )
        assert events_file.exists()
        event = json.loads(events_file.read_text().strip())
        assert event["kind"] == "notify_delivery_failed"
        assert "issue_notification:svc" in event["context"]


class TestSendRecoveryNotification:
    @patch.object(DiscordNotifier, "send_lines", return_value=True)
    def test_sends_recovery(self, mock_send: MagicMock) -> None:
        n = _notifier()
        send_recovery_notification(
            notifier=n,
            state=GlobalState(),
            target_name="svc",
            previous_failures=5,
        )
        mock_send.assert_called_once()
        call_args = mock_send.call_args
        assert call_args[1]["severity"] == "INFO"
        lines = call_args[1]["lines"]
        assert any("previous_consecutive_failures=5" in line for line in lines)

    @patch.object(DiscordNotifier, "send_lines", return_value=False)
    def test_records_failure_event(self, mock_send: MagicMock, tmp_path: Path) -> None:
        n = _notifier()
        events_file = tmp_path / "events.jsonl"
        send_recovery_notification(
            notifier=n,
            state=GlobalState(),
            target_name="svc",
            previous_failures=3,
            events_file=events_file,
            events_max_bytes=5_000_000,
            events_backup_generations=1,
            now_ts=2000.0,
        )
        assert events_file.exists()
        event = json.loads(events_file.read_text().strip())
        assert event["kind"] == "notify_delivery_failed"


class TestSendDueFollowups:
    @patch.object(DiscordNotifier, "send_lines", return_value=True)
    def test_due_followup_sent_and_removed(self, mock_send: MagicMock) -> None:
        n = _notifier()
        state = GlobalState()
        state.followups["svc"] = FollowupRecord(
            due_ts=900.0,
            created_ts=600.0,
            initial_action="restart",
            initial_reason="service down",
            initial_consecutive_failures=3,
        )
        result = _result(healthy=True)
        send_due_followups(
            notifier=n,
            state=state,
            target_results={"svc": result},
            now_ts=1000.0,
        )
        mock_send.assert_called_once()
        assert "svc" not in state.followups

    @patch.object(DiscordNotifier, "send_lines", return_value=True)
    def test_not_yet_due_followup_kept(self, mock_send: MagicMock) -> None:
        n = _notifier()
        state = GlobalState()
        state.followups["svc"] = FollowupRecord(
            due_ts=2000.0,
            created_ts=1000.0,
            initial_action="restart",
            initial_reason="service down",
            initial_consecutive_failures=3,
        )
        send_due_followups(
            notifier=n,
            state=state,
            target_results={},
            now_ts=1500.0,
        )
        mock_send.assert_not_called()
        assert "svc" in state.followups

    @patch.object(DiscordNotifier, "send_lines", return_value=True)
    def test_due_followup_without_result_uses_state(self, mock_send: MagicMock) -> None:
        n = _notifier()
        state = GlobalState()
        ts = state.ensure_target("svc")
        ts.consecutive_failures = 0
        ts.last_failure_reason = "service was down"
        state.followups["svc"] = FollowupRecord(
            due_ts=900.0,
            created_ts=600.0,
            initial_action="warn",
            initial_reason="degraded",
            initial_consecutive_failures=1,
        )
        send_due_followups(
            notifier=n,
            state=state,
            target_results={},
            now_ts=1000.0,
        )
        mock_send.assert_called_once()
        lines = mock_send.call_args[1]["lines"]
        assert any("current_status=healthy" in line for line in lines)

    @patch.object(DiscordNotifier, "send_lines", return_value=False)
    def test_failed_send_keeps_followup_and_records_event(
        self, mock_send: MagicMock, tmp_path: Path
    ) -> None:
        n = _notifier()
        state = GlobalState()
        state.followups["svc"] = FollowupRecord(
            due_ts=900.0,
            created_ts=600.0,
            initial_action="restart",
            initial_reason="down",
            initial_consecutive_failures=3,
        )
        events_file = tmp_path / "events.jsonl"
        send_due_followups(
            notifier=n,
            state=state,
            target_results={"svc": _result(healthy=False)},
            now_ts=1000.0,
            events_file=events_file,
            events_max_bytes=5_000_000,
            events_backup_generations=1,
        )
        assert "svc" in state.followups
        assert events_file.exists()


class TestSendPeriodicHeartbeat:
    @patch.object(DiscordNotifier, "send_lines", return_value=True)
    @patch("raspi_sentinel.cycle_notifications.collect_system_snapshot")
    def test_heartbeat_sent_and_marked(self, mock_snap: MagicMock, mock_send: MagicMock) -> None:
        from raspi_sentinel.notify import SystemSnapshot

        mock_snap.return_value = SystemSnapshot(
            uptime_sec=60000.0,
            load1=0.5,
            load5=0.6,
            load15=0.7,
            disk_used_pct=42.0,
        )
        n = _notifier(heartbeat_interval_sec=300)
        state = GlobalState()
        send_periodic_heartbeat(
            notifier=n,
            state=state,
            target_results={"svc": _result(healthy=True)},
            now_ts=1000.0,
        )
        mock_send.assert_called_once()
        assert state.notify.last_heartbeat_ts == 1000.0

    @patch.object(DiscordNotifier, "send_lines", return_value=True)
    def test_heartbeat_not_sent_within_interval(self, mock_send: MagicMock) -> None:
        n = _notifier(heartbeat_interval_sec=300)
        state = GlobalState()
        state.notify.last_heartbeat_ts = 900.0
        send_periodic_heartbeat(
            notifier=n,
            state=state,
            target_results={},
            now_ts=1100.0,
        )
        mock_send.assert_not_called()

    def test_heartbeat_disabled_when_interval_zero(self) -> None:
        n = _notifier(heartbeat_interval_sec=0)
        state = GlobalState()
        send_periodic_heartbeat(
            notifier=n,
            state=state,
            target_results={},
            now_ts=1000.0,
        )
        assert state.notify.last_heartbeat_ts is None

    @patch.object(DiscordNotifier, "send_lines", return_value=False)
    @patch("raspi_sentinel.cycle_notifications.collect_system_snapshot")
    def test_failed_heartbeat_records_event(
        self, mock_snap: MagicMock, mock_send: MagicMock, tmp_path: Path
    ) -> None:
        from raspi_sentinel.notify import SystemSnapshot

        mock_snap.return_value = SystemSnapshot(
            uptime_sec=100.0, load1=0.0, load5=0.0, load15=0.0, disk_used_pct=0.0
        )
        n = _notifier(heartbeat_interval_sec=300)
        state = GlobalState()
        events_file = tmp_path / "events.jsonl"
        send_periodic_heartbeat(
            notifier=n,
            state=state,
            target_results={},
            now_ts=1000.0,
            events_file=events_file,
            events_max_bytes=5_000_000,
            events_backup_generations=1,
        )
        assert events_file.exists()
        event = json.loads(events_file.read_text().strip())
        assert event["kind"] == "notify_delivery_failed"
        assert event["context"] == "periodic_heartbeat"


class TestDeferredNotificationSummary:
    @patch.object(DiscordNotifier, "send_lines")
    def test_network_failure_is_aggregated_and_summary_sent(
        self, mock_send: MagicMock, tmp_path: Path
    ) -> None:
        n = _notifier(retry_interval_sec=60)
        state = GlobalState()

        def fail_as_network(*args: Any, **kwargs: Any) -> bool:
            n.last_failure_kind = "network"
            return False

        mock_send.side_effect = fail_as_network
        send_issue_notification(
            notifier=n,
            state=state,
            target_name="svc",
            result=_result(healthy=False, failures=[CheckFailure("cmd", "exit 1")]),
            action="warn",
            consecutive_failures=1,
            services=[],
            dry_run=False,
            now_ts=1000.0,
            events_file=tmp_path / "events.jsonl",
            events_max_bytes=5_000_000,
            events_backup_generations=1,
        )
        send_issue_notification(
            notifier=n,
            state=state,
            target_name="svc",
            result=_result(healthy=False, failures=[CheckFailure("cmd", "exit 1")]),
            action="warn",
            consecutive_failures=2,
            services=[],
            dry_run=False,
            now_ts=1020.0,
            events_file=tmp_path / "events2.jsonl",
            events_max_bytes=5_000_000,
            events_backup_generations=1,
        )
        assert state.notify.delivery_backlog is not None
        assert state.notify.delivery_backlog.total_failures == 2

        def succeed(*args: Any, **kwargs: Any) -> bool:
            n.last_failure_kind = None
            return True

        mock_send.side_effect = succeed
        send_delivery_backlog_summary(
            notifier=n,
            state=state,
            now_ts=1061.0,
        )
        assert state.notify.delivery_backlog is None
        assert state.notify.retry_due_ts is None
        summary_call = mock_send.call_args_list[-1]
        assert summary_call[1]["title"] == "Delayed notifications summary"
        lines = summary_call[1]["lines"]
        assert any("delivery_failed_from=" in line for line in lines)
        assert any("delivery_failed_until=" in line for line in lines)
        assert any("failed_notifications_total=2" in line for line in lines)

    @patch.object(DiscordNotifier, "send_lines")
    def test_summary_waits_until_retry_due(self, mock_send: MagicMock) -> None:
        n = _notifier(retry_interval_sec=60)
        state = GlobalState()

        def fail_as_network(*args: Any, **kwargs: Any) -> bool:
            n.last_failure_kind = "network"
            return False

        mock_send.side_effect = fail_as_network
        send_issue_notification(
            notifier=n,
            state=state,
            target_name="svc",
            result=_result(healthy=False, failures=[CheckFailure("cmd", "exit 1")]),
            action="warn",
            consecutive_failures=1,
            services=[],
            dry_run=False,
            now_ts=1000.0,
        )
        calls_after_failure = mock_send.call_count
        send_delivery_backlog_summary(
            notifier=n,
            state=state,
            now_ts=1059.0,
        )
        assert mock_send.call_count == calls_after_failure

    @patch.object(DiscordNotifier, "send_lines")
    def test_summary_failure_extends_failed_until_window(self, mock_send: MagicMock) -> None:
        n = _notifier(retry_interval_sec=60)
        state = GlobalState()

        def fail_as_network(*args: Any, **kwargs: Any) -> bool:
            n.last_failure_kind = "network"
            return False

        mock_send.side_effect = fail_as_network
        send_issue_notification(
            notifier=n,
            state=state,
            target_name="svc",
            result=_result(healthy=False, failures=[CheckFailure("cmd", "exit 1")]),
            action="warn",
            consecutive_failures=1,
            services=[],
            dry_run=False,
            now_ts=1000.0,
        )
        assert state.notify.delivery_backlog is not None
        assert state.notify.delivery_backlog.last_failed_ts == 1000.0
        send_delivery_backlog_summary(
            notifier=n,
            state=state,
            now_ts=1060.0,
        )
        assert state.notify.delivery_backlog is not None
        assert state.notify.delivery_backlog.last_failed_ts == 1060.0

    def test_delivery_backlog_manager_internal_paths(self) -> None:
        state = GlobalState()
        manager = DeliveryBacklogManager(state=state, retry_interval_sec=60)

        # No backlog -> should_send_summary=False
        assert manager.should_send_summary(now_ts=1000.0) is False

        # No backlog branch in mark_summary_network_failure still sets retry_due_ts.
        manager.mark_summary_network_failure(now_ts=1010.0)
        assert state.notify.retry_due_ts == 1070.0

        # Explicit defer branch.
        manager.defer_summary_retry(now_ts=1020.0)
        assert state.notify.retry_due_ts == 1080.0

    def test_delivery_backlog_contexts_are_capped_with_overflow_bucket(self) -> None:
        state = GlobalState()
        manager = DeliveryBacklogManager(state=state, retry_interval_sec=60)
        manager.record_network_failure(context="seed", now_ts=1000.0)
        for idx in range(MAX_NOTIFY_BACKLOG_CONTEXTS + 10):
            manager.record_network_failure(context=f"ctx-{idx}", now_ts=1001.0 + idx)
        backlog = state.notify.delivery_backlog
        assert backlog is not None
        assert len(backlog.contexts) <= MAX_NOTIFY_BACKLOG_CONTEXTS + 1
        assert "__other__" in backlog.contexts

    @patch.object(DiscordNotifier, "send_lines", return_value=False)
    def test_summary_non_network_failure_records_event_and_defers_retry(
        self, mock_send: MagicMock, tmp_path: Path
    ) -> None:
        n = _notifier(retry_interval_sec=60)
        n.last_failure_kind = "http"
        state = GlobalState()

        state.notify.delivery_backlog = NotifyDeliveryBacklog(
            first_failed_ts=1000.0,
            last_failed_ts=1010.0,
            total_failures=6,
            contexts={
                "c1": 1,
                "c2": 1,
                "c3": 1,
                "c4": 1,
                "c5": 1,
                "c6": 1,
            },
        )
        state.notify.retry_due_ts = 1060.0

        events_file = tmp_path / "events.jsonl"
        send_delivery_backlog_summary(
            notifier=n,
            state=state,
            now_ts=1061.0,
            events_file=events_file,
            events_max_bytes=5_000_000,
            events_backup_generations=1,
        )
        assert mock_send.called
        assert events_file.exists()
        assert state.notify.delivery_backlog is not None
        assert state.notify.retry_due_ts == 1121.0
