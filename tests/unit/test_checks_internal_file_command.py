from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from checks_internal_branches_helpers import target

from raspi_sentinel import checks
from raspi_sentinel.checks import command_checks, file_checks
from raspi_sentinel.checks.command_checks import (
    command_check,
    run_command_capture,
    service_active_check,
)
from raspi_sentinel.checks.file_checks import file_freshness_check
from raspi_sentinel.checks.network_probes import parse_ping_stats


def test_file_freshness_missing_and_stale(tmp_path: Path, monkeypatch: Any) -> None:
    missing = file_freshness_check(tmp_path / "missing.txt", 10, "heartbeat_file")
    assert missing is not None and "missing" in missing.message

    p = tmp_path / "f.txt"
    p.write_text("x", encoding="utf-8")
    st = p.stat()
    monkeypatch.setattr(file_checks.time, "time", lambda: st.st_mtime + 100)
    stale = file_freshness_check(p, 10, "heartbeat_file")
    assert stale is not None and "stale" in stale.message

    monkeypatch.setattr(file_checks.time, "time", lambda: st.st_mtime + 1)
    assert file_freshness_check(p, 10, "heartbeat_file") is None


def test_file_freshness_oserror_branch() -> None:
    class DummyPath:
        def stat(self) -> Any:
            raise OSError("boom")

        def __str__(self) -> str:
            return "/dummy"

    failure = file_freshness_check(DummyPath(), 10, "heartbeat_file")  # type: ignore[arg-type]
    assert failure is not None and "cannot stat file" in failure.message


def test_command_check_timeout_oserror_nonzero_and_success(monkeypatch: Any) -> None:
    def timeout_run(*_: Any, **__: Any) -> Any:
        raise subprocess.TimeoutExpired(cmd="x", timeout=1)

    monkeypatch.setattr(command_checks.subprocess, "run", timeout_run)
    assert command_check("x", 1, "command") is not None

    def os_run(*_: Any, **__: Any) -> Any:
        raise OSError("boom")

    monkeypatch.setattr(command_checks.subprocess, "run", os_run)
    assert command_check("x", 1, "command") is not None

    def bad_run(*_: Any, **__: Any) -> Any:
        return subprocess.CompletedProcess(args=["x"], returncode=2, stdout="", stderr="err")

    monkeypatch.setattr(command_checks.subprocess, "run", bad_run)
    failure = command_check("x", 1, "command")
    assert failure is not None and "exit code" in failure.message

    def ok_run(*_: Any, **__: Any) -> Any:
        return subprocess.CompletedProcess(args=["x"], returncode=0, stdout="", stderr="")

    monkeypatch.setattr(command_checks.subprocess, "run", ok_run)
    assert command_check("x", 1, "command") is None


def test_command_check_shell_syntax_is_advisory_without_shell_opt_in() -> None:
    failure = command_check("echo ok | cat", 1, "command", use_shell=False)
    assert failure is None


def test_service_active_check_all_branches(monkeypatch: Any) -> None:
    def timeout_run(*_: Any, **__: Any) -> Any:
        raise subprocess.TimeoutExpired(cmd="systemctl", timeout=10)

    monkeypatch.setattr(command_checks.subprocess, "run", timeout_run)
    assert service_active_check("svc") is not None

    def os_run(*_: Any, **__: Any) -> Any:
        raise OSError("no systemctl")

    monkeypatch.setattr(command_checks.subprocess, "run", os_run)
    assert service_active_check("svc") is not None

    def inactive_run(*_: Any, **__: Any) -> Any:
        return subprocess.CompletedProcess(
            args=["systemctl"], returncode=3, stdout="inactive", stderr=""
        )

    monkeypatch.setattr(command_checks.subprocess, "run", inactive_run)
    assert service_active_check("svc") is not None

    def active_run(*_: Any, **__: Any) -> Any:
        return subprocess.CompletedProcess(args=["systemctl"], returncode=0, stdout="", stderr="")

    monkeypatch.setattr(command_checks.subprocess, "run", active_run)
    assert service_active_check("svc") is None


def test_run_checks_with_command_dns_gateway_and_service(monkeypatch: Any) -> None:
    calls: list[Any] = []

    def fake_run(cmd: Any, **_: Any) -> Any:
        calls.append(cmd)
        if isinstance(cmd, list):
            return subprocess.CompletedProcess(cmd, 0, "", "")
        return subprocess.CompletedProcess([cmd], 0, "", "")

    monkeypatch.setattr(checks.subprocess, "run", fake_run)
    result = checks.run_checks(
        target(
            services=["svc"],
            service_active=True,
            command="true",
            command_timeout_sec=1,
            dns_check_command="true",
            gateway_check_command="true",
            dependency_check_timeout_sec=1,
        )
    )
    assert result.healthy
    assert len(calls) >= 4


def test_run_checks_with_extended_dependency_commands(monkeypatch: Any) -> None:
    def fake_run(cmd: Any, **_: Any) -> Any:
        return subprocess.CompletedProcess([cmd], 0, "", "")

    monkeypatch.setattr(checks.subprocess, "run", fake_run)
    result = checks.run_checks(
        target(
            link_check_command="true",
            default_route_check_command="true",
            internet_ip_check_command="true",
            dns_server_check_command="true",
            wan_vs_target_check_command="true",
            dependency_check_timeout_sec=1,
        )
    )
    assert result.healthy
    assert result.observations["link_ok"] is True
    assert result.observations["default_route_ok"] is True
    assert result.observations["internet_ip_ok"] is True
    assert result.observations["dns_server_reachable"] is True
    assert result.observations["wan_vs_target_ok"] is True


def test_run_checks_heartbeat_output_and_service_failure(tmp_path: Path, monkeypatch: Any) -> None:
    hb = tmp_path / "hb.txt"
    out = tmp_path / "out.txt"
    hb.write_text("ok", encoding="utf-8")
    out.write_text("ok", encoding="utf-8")

    def fake_run(cmd: Any, **_: Any) -> Any:
        if isinstance(cmd, list):
            return subprocess.CompletedProcess(cmd, 1, "inactive", "")
        return subprocess.CompletedProcess([cmd], 0, "", "")

    monkeypatch.setattr(checks.subprocess, "run", fake_run)
    result = checks.run_checks(
        target(
            services=["svc"],
            service_active=True,
            heartbeat_file=hb,
            heartbeat_max_age_sec=3600,
            output_file=out,
            output_max_age_sec=3600,
            command="true",
            command_timeout_sec=1,
            dns_check_command="true",
            gateway_check_command="true",
            dependency_check_timeout_sec=1,
        )
    )
    assert not result.healthy
    assert any(f.check == "service_active" for f in result.failures)


def test_run_command_capture_and_ping_parser_branches(monkeypatch: Any) -> None:
    def timeout_run(*_: Any, **__: Any) -> Any:
        raise subprocess.TimeoutExpired(cmd="x", timeout=1)

    monkeypatch.setattr(command_checks.subprocess, "run", timeout_run)
    result, error = run_command_capture(["echo", "x"], timeout_sec=1)
    assert result is None and error == "timeout"

    def os_run(*_: Any, **__: Any) -> Any:
        raise OSError("missing")

    monkeypatch.setattr(command_checks.subprocess, "run", os_run)
    result, error = run_command_capture(["echo", "x"], timeout_sec=1)
    assert result is None and error == "unavailable"

    def ok_run(*_: Any, **__: Any) -> Any:
        return subprocess.CompletedProcess(args=["echo"], returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr(command_checks.subprocess, "run", ok_run)
    result, error = run_command_capture(["echo", "x"], timeout_sec=1)
    assert result is not None and error is None

    latency, loss = parse_ping_stats(
        (
            "3 packets transmitted, 3 received, 0% packet loss\n"
            "rtt min/avg/max/mdev = 1.0/2.0/3.0/0.5 ms"
        )
    )
    assert latency == 2.0
    assert loss == 0.0
