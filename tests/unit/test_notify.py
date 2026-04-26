from __future__ import annotations

import json
import urllib.error
from io import BytesIO
from typing import Any
from unittest.mock import MagicMock, patch

from conftest import make_discord_config

from raspi_sentinel.checks import CheckFailure, CheckResult
from raspi_sentinel.notify import (
    DiscordNotifier,
    collect_system_snapshot,
    format_failures,
    mark_heartbeat_sent,
    should_send_periodic_heartbeat,
)
from raspi_sentinel.state_models import GlobalState


def _notifier(enabled: bool = True, **overrides: Any) -> DiscordNotifier:
    return DiscordNotifier(
        make_discord_config(
            enabled=enabled,
            webhook_url="https://discord.com/api/webhooks/test/token" if enabled else None,
            **overrides,
        )
    )


def _result(healthy: bool = True, failures: list[CheckFailure] | None = None) -> CheckResult:
    return CheckResult(
        target="demo",
        healthy=healthy,
        failures=failures or [],
    )


class TestDiscordNotifierEnabled:
    def test_enabled_when_url_and_flag(self) -> None:
        n = _notifier(enabled=True)
        assert n.enabled is True

    def test_disabled_when_no_url(self) -> None:
        n = DiscordNotifier(make_discord_config(enabled=True, webhook_url=None))
        assert n.enabled is False

    def test_disabled_when_flag_false(self) -> None:
        n = _notifier(enabled=False)
        assert n.enabled is False


class TestSendLines:
    def test_send_returns_false_when_disabled(self) -> None:
        n = _notifier(enabled=False)
        assert n.send_lines("title", ["line"]) is False

    @patch("raspi_sentinel.notify.urllib.request.urlopen")
    def test_successful_send(self, mock_urlopen: MagicMock) -> None:
        mock_resp = MagicMock()
        mock_resp.status = 204
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        n = _notifier()
        assert n.send_lines("title", ["line1", "line2"]) is True
        assert mock_urlopen.call_count == 1

        req = mock_urlopen.call_args[0][0]
        payload = json.loads(req.data)
        assert payload["username"] == "raspi-sentinel"
        assert "[INFO] title" in payload["content"]
        assert "- line1" in payload["content"]

    @patch("raspi_sentinel.notify.urllib.request.urlopen")
    def test_content_truncation_at_1900_chars(self, mock_urlopen: MagicMock) -> None:
        mock_resp = MagicMock()
        mock_resp.status = 204
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        n = _notifier()
        long_lines = [f"line {'x' * 200}" for _ in range(20)]
        assert n.send_lines("title", long_lines) is True

        req = mock_urlopen.call_args[0][0]
        payload = json.loads(req.data)
        assert len(payload["content"]) <= 1900

    @patch("raspi_sentinel.notify.urllib.request.urlopen")
    def test_retry_on_http_error_without_retry_after(self, mock_urlopen: MagicMock) -> None:
        mock_resp_body = BytesIO(b'{"message": "rate limited"}')
        headers = MagicMock()
        headers.get.return_value = None
        exc = urllib.error.HTTPError(
            url="https://example.com",
            code=429,
            msg="Too Many Requests",
            hdrs=headers,
            fp=mock_resp_body,
        )
        mock_urlopen.side_effect = exc

        n = _notifier(timeout_sec=1)
        with patch("raspi_sentinel.notify.time.sleep"):
            result = n.send_lines("title", ["line"])
        assert result is False
        assert mock_urlopen.call_count == 3

    @patch("raspi_sentinel.notify.urllib.request.urlopen")
    def test_retry_backoff_uses_configured_base(self, mock_urlopen: MagicMock) -> None:
        mock_resp_body = BytesIO(b'{"message": "rate limited"}')
        headers = MagicMock()
        headers.get.return_value = None
        exc = urllib.error.HTTPError(
            url="https://example.com",
            code=429,
            msg="Too Many Requests",
            hdrs=headers,
            fp=mock_resp_body,
        )
        mock_urlopen.side_effect = exc
        n = _notifier(timeout_sec=1, retry_backoff_base_sec=1.25)
        sleep_calls: list[float] = []
        with patch("raspi_sentinel.notify.time.sleep", side_effect=lambda s: sleep_calls.append(s)):
            result = n.send_lines("title", ["line"])
        assert result is False
        assert sleep_calls == [1.25, 2.5]

    @patch("raspi_sentinel.notify.urllib.request.urlopen")
    def test_retry_respects_retry_after_header(self, mock_urlopen: MagicMock) -> None:
        headers = MagicMock()
        headers.get.return_value = "1.5"
        exc = urllib.error.HTTPError(
            url="https://example.com",
            code=429,
            msg="Too Many Requests",
            hdrs=headers,
            fp=BytesIO(b"{}"),
        )
        mock_urlopen.side_effect = exc

        n = _notifier(timeout_sec=1)
        sleep_calls: list[float] = []
        with patch("raspi_sentinel.notify.time.sleep", side_effect=lambda s: sleep_calls.append(s)):
            n.send_lines("title", ["line"])
        assert any(s >= 1.5 for s in sleep_calls)

    @patch("raspi_sentinel.notify.urllib.request.urlopen")
    def test_url_error_returns_false(self, mock_urlopen: MagicMock) -> None:
        mock_urlopen.side_effect = urllib.error.URLError("connection refused")
        n = _notifier(timeout_sec=1)
        with patch("raspi_sentinel.notify.time.sleep"):
            assert n.send_lines("title", ["line"]) is False

    @patch("raspi_sentinel.notify.urllib.request.urlopen")
    def test_timeout_error_returns_false(self, mock_urlopen: MagicMock) -> None:
        mock_urlopen.side_effect = TimeoutError("timed out")
        n = _notifier(timeout_sec=1)
        with patch("raspi_sentinel.notify.time.sleep"):
            assert n.send_lines("title", ["line"]) is False

    @patch("raspi_sentinel.notify.urllib.request.urlopen")
    def test_oserror_returns_false(self, mock_urlopen: MagicMock) -> None:
        mock_urlopen.side_effect = OSError("broken pipe")
        n = _notifier(timeout_sec=1)
        with patch("raspi_sentinel.notify.time.sleep"):
            assert n.send_lines("title", ["line"]) is False

    @patch("raspi_sentinel.notify.urllib.request.urlopen")
    def test_non_2xx_status_retries(self, mock_urlopen: MagicMock) -> None:
        mock_resp = MagicMock()
        mock_resp.status = 500
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        n = _notifier(timeout_sec=1)
        with patch("raspi_sentinel.notify.time.sleep"):
            result = n.send_lines("title", ["line"])
        assert result is False
        assert mock_urlopen.call_count == 3

    @patch("raspi_sentinel.notify.urllib.request.urlopen")
    def test_severity_is_included(self, mock_urlopen: MagicMock) -> None:
        mock_resp = MagicMock()
        mock_resp.status = 204
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        n = _notifier()
        n.send_lines("alert", ["detail"], severity="ERROR")
        req = mock_urlopen.call_args[0][0]
        payload = json.loads(req.data)
        assert "[ERROR] alert" in payload["content"]


class TestCollectSystemSnapshot:
    @patch("raspi_sentinel.notify.shutil.disk_usage")
    @patch("raspi_sentinel.notify.os.getloadavg", return_value=(0.5, 0.6, 0.7))
    @patch("raspi_sentinel.notify.read_uptime_sec", return_value=12345.0)
    def test_snapshot_collects_all_fields(
        self,
        mock_uptime: MagicMock,
        mock_loadavg: MagicMock,
        mock_disk: MagicMock,
    ) -> None:
        mock_disk.return_value = MagicMock(total=1000, used=600, free=400)
        snap = collect_system_snapshot()
        assert snap.uptime_sec == 12345.0
        assert snap.load1 == 0.5
        assert snap.disk_used_pct == 60.0

    @patch("raspi_sentinel.notify.shutil.disk_usage")
    @patch("raspi_sentinel.notify.os.getloadavg", side_effect=OSError)
    @patch("raspi_sentinel.notify.read_uptime_sec", return_value=0.0)
    def test_snapshot_handles_loadavg_failure(
        self,
        mock_uptime: MagicMock,
        mock_loadavg: MagicMock,
        mock_disk: MagicMock,
    ) -> None:
        mock_disk.return_value = MagicMock(total=1000, used=100, free=900)
        snap = collect_system_snapshot()
        assert snap.load1 == 0.0
        assert snap.load5 == 0.0

    @patch("raspi_sentinel.notify.shutil.disk_usage")
    @patch("raspi_sentinel.notify.os.getloadavg", return_value=(0.0, 0.0, 0.0))
    @patch("raspi_sentinel.notify.read_uptime_sec", return_value=0.0)
    def test_snapshot_zero_disk_total(
        self,
        mock_uptime: MagicMock,
        mock_loadavg: MagicMock,
        mock_disk: MagicMock,
    ) -> None:
        mock_disk.return_value = MagicMock(total=0, used=0, free=0)
        snap = collect_system_snapshot()
        assert snap.disk_used_pct == 0.0


class TestFormatFailures:
    def test_healthy_returns_none(self) -> None:
        assert format_failures(_result(healthy=True)) == "none"

    def test_with_policy_reason(self) -> None:
        r = _result(healthy=False, failures=[CheckFailure("x", "msg")])
        r.observations["policy_reason"] = "dns_error"
        assert format_failures(r) == "dns_error"

    def test_with_failures_only(self) -> None:
        r = _result(healthy=False, failures=[CheckFailure("svc", "down")])
        text = format_failures(r)
        assert "svc: down" in text

    def test_empty_failures_fallback(self) -> None:
        r = _result(healthy=False, failures=[])
        assert format_failures(r) == "unhealthy"


class TestHeartbeat:
    def test_first_heartbeat_always_sends(self) -> None:
        state = GlobalState()
        assert should_send_periodic_heartbeat(state, interval_sec=300, now_ts=1000.0)

    def test_within_interval_does_not_send(self) -> None:
        state = GlobalState()
        state.notify.last_heartbeat_ts = 900.0
        assert not should_send_periodic_heartbeat(state, interval_sec=300, now_ts=1100.0)

    def test_past_interval_sends(self) -> None:
        state = GlobalState()
        state.notify.last_heartbeat_ts = 500.0
        assert should_send_periodic_heartbeat(state, interval_sec=300, now_ts=900.0)

    def test_invalid_ts_sends(self) -> None:
        state = GlobalState()
        state.notify.last_heartbeat_ts = None
        assert should_send_periodic_heartbeat(state, interval_sec=300, now_ts=1000.0)

    def test_mark_heartbeat_sent_updates_state(self) -> None:
        state = GlobalState()
        mark_heartbeat_sent(state, now_ts=1234.5)
        assert state.notify.last_heartbeat_ts == 1234.5
