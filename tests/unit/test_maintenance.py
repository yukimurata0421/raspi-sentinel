from __future__ import annotations

import subprocess
from typing import Any
from unittest.mock import MagicMock, patch

from conftest import make_target

from raspi_sentinel.maintenance import is_target_suppressed_by_maintenance, run_command_success
from raspi_sentinel.state_models import TargetState


class TestRunCommandSuccess:
    def test_simple_command_succeeds(self) -> None:
        assert run_command_success("true", timeout_sec=5, use_shell=False) is True

    def test_simple_command_fails(self) -> None:
        assert run_command_success("false", timeout_sec=5, use_shell=False) is False

    def test_shell_syntax_is_advisory_without_use_shell(self, caplog: Any) -> None:
        with caplog.at_level("WARNING", logger="raspi_sentinel.maintenance"):
            result = run_command_success("echo hello && echo world", timeout_sec=5, use_shell=False)
        assert result is True
        assert "possible shell syntax detected with use_shell=false" in caplog.text

    def test_shell_syntax_accepted_with_use_shell(self) -> None:
        result = run_command_success("echo hello && echo world", timeout_sec=5, use_shell=True)
        assert result is True

    def test_pipe_runs_without_shell(self) -> None:
        assert run_command_success("echo x | cat", timeout_sec=5, use_shell=False) is True

    def test_backtick_runs_without_shell(self) -> None:
        assert run_command_success("echo `date`", timeout_sec=5, use_shell=False) is True

    def test_dollar_paren_runs_without_shell(self) -> None:
        assert run_command_success("echo $(date)", timeout_sec=5, use_shell=False) is True

    def test_semicolon_runs_without_shell(self) -> None:
        assert run_command_success("echo a; echo b", timeout_sec=5, use_shell=False) is True

    @patch(
        "raspi_sentinel.maintenance.subprocess.run",
        side_effect=subprocess.TimeoutExpired("cmd", 1),
    )
    def test_timeout_returns_false(self, mock_run: MagicMock) -> None:
        assert run_command_success("sleep 100", timeout_sec=1, use_shell=False) is False

    @patch("raspi_sentinel.maintenance.subprocess.run", side_effect=OSError("not found"))
    def test_oserror_returns_false(self, mock_run: MagicMock) -> None:
        assert run_command_success("nonexistent_binary", timeout_sec=5, use_shell=False) is False

    def test_empty_command_after_split(self) -> None:
        assert run_command_success("", timeout_sec=5, use_shell=False) is False

    def test_empty_command_logs_warning(self, caplog: Any) -> None:
        with caplog.at_level("WARNING", logger="raspi_sentinel.maintenance"):
            assert run_command_success("   ", timeout_sec=5, use_shell=False) is False
        assert "maintenance command is empty after parsing" in caplog.text

    def test_invalid_shlex_syntax(self) -> None:
        assert run_command_success("echo 'unterminated", timeout_sec=5, use_shell=False) is False


class TestIsTargetSuppressedByMaintenance:
    def test_no_command_not_suppressed(self) -> None:
        target = make_target(maintenance_mode_command=None)
        state = TargetState()
        suppressed, reason = is_target_suppressed_by_maintenance(target, state, now_ts=1000.0)
        assert suppressed is False
        assert reason == ""

    def test_grace_period_active(self) -> None:
        target = make_target(maintenance_mode_command="true")
        state = TargetState(maintenance_suppress_until_ts=2000.0)
        suppressed, reason = is_target_suppressed_by_maintenance(target, state, now_ts=1500.0)
        assert suppressed is True
        assert "grace active" in reason
        assert "500s remaining" in reason

    def test_grace_period_expired_command_checked(self) -> None:
        target = make_target(
            maintenance_mode_command="true",
            maintenance_mode_timeout_sec=5,
            maintenance_mode_use_shell=False,
        )
        state = TargetState(maintenance_suppress_until_ts=900.0)
        suppressed, reason = is_target_suppressed_by_maintenance(target, state, now_ts=1000.0)
        assert suppressed is True
        assert "command matched" in reason

    def test_command_fails_not_suppressed(self) -> None:
        target = make_target(
            maintenance_mode_command="false",
            maintenance_mode_timeout_sec=5,
            maintenance_mode_use_shell=False,
        )
        state = TargetState()
        suppressed, reason = is_target_suppressed_by_maintenance(target, state, now_ts=1000.0)
        assert suppressed is False

    def test_grace_sec_sets_suppress_until(self) -> None:
        target = make_target(
            maintenance_mode_command="true",
            maintenance_mode_timeout_sec=5,
            maintenance_mode_use_shell=False,
            maintenance_grace_sec=600,
        )
        state = TargetState()
        suppressed, _ = is_target_suppressed_by_maintenance(target, state, now_ts=1000.0)
        assert suppressed is True
        assert state.maintenance_suppress_until_ts == 1600.0

    def test_no_grace_sec_does_not_set_suppress_until(self) -> None:
        target = make_target(
            maintenance_mode_command="true",
            maintenance_mode_timeout_sec=5,
            maintenance_mode_use_shell=False,
            maintenance_grace_sec=None,
        )
        state = TargetState()
        suppressed, _ = is_target_suppressed_by_maintenance(target, state, now_ts=1000.0)
        assert suppressed is True
        assert state.maintenance_suppress_until_ts is None

    def test_shell_command_with_use_shell(self) -> None:
        target = make_target(
            maintenance_mode_command="echo ok && true",
            maintenance_mode_timeout_sec=5,
            maintenance_mode_use_shell=True,
        )
        state = TargetState()
        suppressed, _ = is_target_suppressed_by_maintenance(target, state, now_ts=1000.0)
        assert suppressed is True

    def test_default_timeout_when_none(self) -> None:
        target = make_target(
            maintenance_mode_command="true",
            maintenance_mode_timeout_sec=None,
        )
        state = TargetState()
        suppressed, _ = is_target_suppressed_by_maintenance(target, state, now_ts=1000.0)
        assert suppressed is True
