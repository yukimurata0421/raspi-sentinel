from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from raspi_sentinel import checks
from raspi_sentinel.config import TargetConfig


def _target(**overrides: Any) -> TargetConfig:
    base = {
        "name": "demo",
        "services": [],
        "service_active": False,
        "heartbeat_file": None,
        "heartbeat_max_age_sec": None,
        "output_file": None,
        "output_max_age_sec": None,
        "command": None,
        "command_timeout_sec": None,
        "dns_check_command": None,
        "gateway_check_command": None,
        "dependency_check_timeout_sec": None,
        "stats_file": None,
        "stats_updated_max_age_sec": None,
        "stats_last_input_max_age_sec": None,
        "stats_last_success_max_age_sec": None,
        "stats_records_stall_cycles": None,
        "time_health_enabled": False,
        "check_interval_threshold_sec": 30,
        "wall_clock_freeze_min_monotonic_sec": 25,
        "wall_clock_freeze_max_wall_advance_sec": 1,
        "wall_clock_drift_threshold_sec": 30,
        "http_time_probe_url": None,
        "http_time_probe_timeout_sec": 5,
        "clock_skew_threshold_sec": 300,
        "clock_anomaly_reboot_consecutive": 3,
        "maintenance_mode_command": None,
        "maintenance_mode_timeout_sec": None,
        "maintenance_grace_sec": None,
        "restart_threshold": None,
        "reboot_threshold": None,
    }
    base.update(overrides)
    return TargetConfig(**base)


def test_file_freshness_missing_and_stale(tmp_path: Path, monkeypatch: Any) -> None:
    missing = checks._file_freshness_check(tmp_path / "missing.txt", 10, "heartbeat_file")
    assert missing is not None and "missing" in missing.message

    p = tmp_path / "f.txt"
    p.write_text("x", encoding="utf-8")
    st = p.stat()
    monkeypatch.setattr(checks.time, "time", lambda: st.st_mtime + 100)
    stale = checks._file_freshness_check(p, 10, "heartbeat_file")
    assert stale is not None and "stale" in stale.message

    monkeypatch.setattr(checks.time, "time", lambda: st.st_mtime + 1)
    assert checks._file_freshness_check(p, 10, "heartbeat_file") is None


def test_file_freshness_oserror_branch() -> None:
    class DummyPath:
        def stat(self) -> Any:
            raise OSError("boom")

        def __str__(self) -> str:
            return "/dummy"

    failure = checks._file_freshness_check(DummyPath(), 10, "heartbeat_file")  # type: ignore[arg-type]
    assert failure is not None and "cannot stat file" in failure.message


def test_command_check_timeout_oserror_nonzero_and_success(monkeypatch: Any) -> None:
    def timeout_run(*_: Any, **__: Any) -> Any:
        raise subprocess.TimeoutExpired(cmd="x", timeout=1)

    monkeypatch.setattr(checks.subprocess, "run", timeout_run)
    assert checks._command_check("x", 1, "command") is not None

    def os_run(*_: Any, **__: Any) -> Any:
        raise OSError("boom")

    monkeypatch.setattr(checks.subprocess, "run", os_run)
    assert checks._command_check("x", 1, "command") is not None

    def bad_run(*_: Any, **__: Any) -> Any:
        return subprocess.CompletedProcess(args=["x"], returncode=2, stdout="", stderr="err")

    monkeypatch.setattr(checks.subprocess, "run", bad_run)
    failure = checks._command_check("x", 1, "command")
    assert failure is not None and "exit code" in failure.message

    def ok_run(*_: Any, **__: Any) -> Any:
        return subprocess.CompletedProcess(args=["x"], returncode=0, stdout="", stderr="")

    monkeypatch.setattr(checks.subprocess, "run", ok_run)
    assert checks._command_check("x", 1, "command") is None


def test_service_active_check_all_branches(monkeypatch: Any) -> None:
    def timeout_run(*_: Any, **__: Any) -> Any:
        raise subprocess.TimeoutExpired(cmd="systemctl", timeout=10)

    monkeypatch.setattr(checks.subprocess, "run", timeout_run)
    assert checks._service_active_check("svc") is not None

    def os_run(*_: Any, **__: Any) -> Any:
        raise OSError("no systemctl")

    monkeypatch.setattr(checks.subprocess, "run", os_run)
    assert checks._service_active_check("svc") is not None

    def inactive_run(*_: Any, **__: Any) -> Any:
        return subprocess.CompletedProcess(
            args=["systemctl"], returncode=3, stdout="inactive", stderr=""
        )

    monkeypatch.setattr(checks.subprocess, "run", inactive_run)
    assert checks._service_active_check("svc") is not None

    def active_run(*_: Any, **__: Any) -> Any:
        return subprocess.CompletedProcess(args=["systemctl"], returncode=0, stdout="", stderr="")

    monkeypatch.setattr(checks.subprocess, "run", active_run)
    assert checks._service_active_check("svc") is None


def test_stats_schema_branches_for_invalid_fields(tmp_path: Path) -> None:
    now = datetime.now(timezone.utc).isoformat()
    stats = {
        "updated_at": now,
        "last_input_ts": now,
        "last_success_ts": now,
        "status": 1,
        "records_processed_total": "x",
        "dns_ok": "x",
        "gateway_ok": "x",
    }
    p = tmp_path / "stats.json"
    p.write_text(json.dumps(stats), encoding="utf-8")
    result = checks.run_checks(
        _target(
            stats_file=p,
            stats_updated_max_age_sec=120,
            stats_last_input_max_age_sec=120,
            stats_last_success_max_age_sec=120,
        )
    )
    names = {f.check for f in result.failures}
    assert "semantic_status" in names
    assert "semantic_records_total" in names
    assert "dependency_dns" in names
    assert "dependency_gateway" in names


def test_stats_schema_marks_unhealthy_status_and_false_dependency_flags(tmp_path: Path) -> None:
    now = datetime.now(timezone.utc).isoformat()
    p = tmp_path / "stats.json"
    p.write_text(
        json.dumps(
            {
                "updated_at": now,
                "status": "degraded",
                "dns_ok": False,
                "gateway_ok": False,
            }
        ),
        encoding="utf-8",
    )
    result = checks.run_checks(_target(stats_file=p, stats_updated_max_age_sec=120))
    names = {f.check for f in result.failures}
    assert "semantic_status" in names
    assert "dependency_dns" in names
    assert "dependency_gateway" in names


def test_stats_file_read_oserror_and_non_object(tmp_path: Path) -> None:
    d = tmp_path / "dir_as_stats"
    d.mkdir()
    result = checks.run_checks(_target(stats_file=d, stats_updated_max_age_sec=10))
    assert any(f.check == "semantic_stats_file" for f in result.failures)

    p = tmp_path / "stats.json"
    p.write_text(json.dumps([1, 2, 3]), encoding="utf-8")
    result = checks.run_checks(_target(stats_file=p, stats_updated_max_age_sec=10))
    assert any(f.check == "semantic_stats_file" for f in result.failures)


def test_stats_timestamp_format_branches(tmp_path: Path) -> None:
    p = tmp_path / "stats.json"
    p.write_text(json.dumps({"updated_at": "invalid"}), encoding="utf-8")
    result = checks.run_checks(_target(stats_file=p, stats_updated_max_age_sec=10))
    assert any("invalid timestamp format" in f.message for f in result.failures)

    p.write_text(json.dumps({"updated_at": "2026-04-10T10:00:00"}), encoding="utf-8")
    result = checks.run_checks(_target(stats_file=p, stats_updated_max_age_sec=10))
    assert any("timezone offset" in f.message for f in result.failures)

    now_z = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    p.write_text(json.dumps({"updated_at": now_z}), encoding="utf-8")
    result = checks.run_checks(_target(stats_file=p, stats_updated_max_age_sec=3600))
    assert result.healthy


def test_stats_last_input_stale_branch(tmp_path: Path) -> None:
    now = datetime.now(timezone.utc)
    p = tmp_path / "stats.json"
    p.write_text(
        json.dumps(
            {
                "updated_at": now.isoformat(),
                "last_input_ts": (now.replace(year=2025)).isoformat(),
                "last_success_ts": now.isoformat(),
            }
        ),
        encoding="utf-8",
    )
    result = checks.run_checks(
        _target(
            stats_file=p,
            stats_updated_max_age_sec=3600,
            stats_last_input_max_age_sec=10,
            stats_last_success_max_age_sec=3600,
        )
    )
    assert any(f.check == "semantic_last_input_ts" for f in result.failures)


def test_run_checks_with_command_dns_gateway_and_service(monkeypatch: Any) -> None:
    calls: list[Any] = []

    def fake_run(cmd: Any, **_: Any) -> Any:
        calls.append(cmd)
        if isinstance(cmd, list):
            return subprocess.CompletedProcess(cmd, 0, "", "")
        return subprocess.CompletedProcess([cmd], 0, "", "")

    monkeypatch.setattr(checks.subprocess, "run", fake_run)
    result = checks.run_checks(
        _target(
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
        _target(
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


def test_stats_checks_handles_none_payload(monkeypatch: Any) -> None:
    monkeypatch.setattr(checks, "_load_stats", lambda path: (None, None))
    failures: list[checks.CheckFailure] = []
    obs: dict[str, Any] = {}
    checks._stats_checks(
        target=_target(stats_file=Path("/tmp/unused.json"), stats_updated_max_age_sec=10),
        failures=failures,
        observations=obs,
    )
    assert failures == []
