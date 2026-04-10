from __future__ import annotations

import subprocess
from typing import Any
from urllib import error

from raspi_sentinel import time_health
from raspi_sentinel.checks import CheckFailure, CheckResult
from raspi_sentinel.config import TargetConfig
from raspi_sentinel.time_health import apply_time_health_checks


def _target(**overrides: Any) -> TargetConfig:
    base = {
        "name": "clock_target",
        "services": [],
        "service_active": False,
        "heartbeat_file": None,
        "heartbeat_max_age_sec": None,
        "output_file": None,
        "output_max_age_sec": None,
        "command": None,
        "command_use_shell": False,
        "command_timeout_sec": None,
        "dns_check_command": None,
        "dns_check_use_shell": False,
        "gateway_check_command": None,
        "gateway_check_use_shell": False,
        "dependency_check_timeout_sec": None,
        "stats_file": None,
        "stats_updated_max_age_sec": None,
        "stats_last_input_max_age_sec": None,
        "stats_last_success_max_age_sec": None,
        "stats_records_stall_cycles": None,
        "time_health_enabled": True,
        "check_interval_threshold_sec": 30,
        "wall_clock_freeze_min_monotonic_sec": 25,
        "wall_clock_freeze_max_wall_advance_sec": 1,
        "wall_clock_drift_threshold_sec": 30,
        "http_time_probe_url": None,
        "http_time_probe_timeout_sec": 5,
        "clock_skew_threshold_sec": 300,
        "clock_anomaly_reboot_consecutive": 3,
        "maintenance_mode_command": None,
        "maintenance_mode_use_shell": False,
        "maintenance_mode_timeout_sec": None,
        "maintenance_grace_sec": None,
        "restart_threshold": 1,
        "reboot_threshold": 3,
    }
    base.update(overrides)
    return TargetConfig(**base)


def test_time_health_detects_frozen_clock(monkeypatch: Any) -> None:
    monkeypatch.setattr("raspi_sentinel.time_health.time.monotonic", lambda: 130.0)
    monkeypatch.setattr("raspi_sentinel.time_health._query_ntp_sync_ok", lambda timeout_sec=3: None)

    state = {
        "clock_prev_wall_time_epoch": 1000.0,
        "clock_prev_monotonic_sec": 100.0,
    }
    result = CheckResult(target="clock_target", healthy=True, failures=[])
    apply_time_health_checks(
        target=_target(clock_anomaly_reboot_consecutive=1),
        target_state=state,
        result=result,
        now_wall_ts=1000.1,
    )

    assert result.observations["clock_frozen_detected"] is True
    assert result.observations["clock_reason"] in (
        "clock_frozen",
        "clock_frozen_persistent",
    )
    assert result.observations["delta_monotonic_sec"] == 30.0
    assert result.observations["consecutive_clock_freeze_count"] == 1
    assert not result.observations["clock_reboot_ready"]


def test_time_health_detects_http_clock_skew(monkeypatch: Any) -> None:
    monkeypatch.setattr("raspi_sentinel.time_health.time.monotonic", lambda: 210.0)
    monkeypatch.setattr(
        "raspi_sentinel.time_health._fetch_http_date_epoch",
        lambda url, timeout_sec: (2600.0, None),
    )
    monkeypatch.setattr(
        "raspi_sentinel.time_health._query_ntp_sync_ok", lambda timeout_sec=3: False
    )

    state = {
        "clock_prev_wall_time_epoch": 1990.0,
        "clock_prev_monotonic_sec": 200.0,
    }
    result = CheckResult(target="clock_target", healthy=True, failures=[])
    apply_time_health_checks(
        target=_target(
            http_time_probe_url="https://www.google.com",
            clock_skew_threshold_sec=300,
            clock_anomaly_reboot_consecutive=1,
        ),
        target_state=state,
        result=result,
        now_wall_ts=2000.0,
    )

    assert result.observations["clock_skew_detected"] is True
    assert result.observations["http_probe_ok"]
    assert result.observations["http_time_skew_sec"] == 600.0
    assert result.observations["ntp_sync_ok"] is False
    assert result.observations["clock_reason"] == "time_sync_broken_skewed"
    assert not result.observations["clock_reboot_ready"]


def test_time_health_dependency_failure_blocks_clock_reboot(monkeypatch: Any) -> None:
    monkeypatch.setattr("raspi_sentinel.time_health.time.monotonic", lambda: 150.0)
    monkeypatch.setattr("raspi_sentinel.time_health._query_ntp_sync_ok", lambda timeout_sec=3: None)

    state = {
        "clock_prev_wall_time_epoch": 1000.0,
        "clock_prev_monotonic_sec": 100.0,
    }
    result = CheckResult(
        target="clock_target",
        healthy=False,
        failures=[CheckFailure("dependency_dns", "dns failed")],
    )
    apply_time_health_checks(
        target=_target(clock_anomaly_reboot_consecutive=1),
        target_state=state,
        result=result,
        now_wall_ts=1000.1,
    )

    assert result.observations["clock_frozen_detected"] is True
    assert not result.observations["clock_reboot_ready"]


def test_fetch_http_date_epoch_branches(monkeypatch: Any) -> None:
    class DummyResponse:
        def __init__(self, date_header: str | None) -> None:
            self.headers = {"Date": date_header} if date_header is not None else {}

        def __enter__(self) -> "DummyResponse":
            return self

        def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> bool:
            return False

    monkeypatch.setattr(
        time_health.request,
        "urlopen",
        lambda req, timeout: DummyResponse("Fri, 10 Apr 2026 01:00:00 GMT"),
    )
    epoch, err = time_health._fetch_http_date_epoch("https://example.com", 2)
    assert epoch is not None and err is None

    monkeypatch.setattr(
        time_health.request,
        "urlopen",
        lambda req, timeout: DummyResponse(None),
    )
    epoch, err = time_health._fetch_http_date_epoch("https://example.com", 2)
    assert epoch is None and err == "date header missing"

    monkeypatch.setattr(
        time_health.request,
        "urlopen",
        lambda req, timeout: DummyResponse("not-a-date"),
    )
    epoch, err = time_health._fetch_http_date_epoch("https://example.com", 2)
    assert epoch is None and err == "date header parse failed"


def test_fetch_http_date_epoch_http_error_branches(monkeypatch: Any) -> None:
    def raise_http_error_no_date(req: Any, timeout: int) -> Any:
        raise error.HTTPError(req.full_url, 503, "x", hdrs={}, fp=None)

    monkeypatch.setattr(time_health.request, "urlopen", raise_http_error_no_date)
    epoch, err = time_health._fetch_http_date_epoch("https://example.com", 2)
    assert epoch is None and err == "http error status=503"

    def raise_http_error_with_date(req: Any, timeout: int) -> Any:
        raise error.HTTPError(
            req.full_url,
            500,
            "x",
            hdrs={"Date": "Fri, 10 Apr 2026 01:00:00 GMT"},
            fp=None,
        )

    monkeypatch.setattr(time_health.request, "urlopen", raise_http_error_with_date)
    epoch, err = time_health._fetch_http_date_epoch("https://example.com", 2)
    assert epoch is not None and err is None

    monkeypatch.setattr(
        time_health.request,
        "urlopen",
        lambda req, timeout: (_ for _ in ()).throw(Exception("boom")),
    )
    epoch, err = time_health._fetch_http_date_epoch("https://example.com", 2)
    assert epoch is None and err == "boom"


def test_query_ntp_sync_branches(monkeypatch: Any) -> None:
    monkeypatch.setattr(
        time_health.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args=["timedatectl"], returncode=0, stdout="true\n", stderr=""
        ),
    )
    assert time_health._query_ntp_sync_ok() is True

    monkeypatch.setattr(
        time_health.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args=["timedatectl"], returncode=0, stdout="false\n", stderr=""
        ),
    )
    assert time_health._query_ntp_sync_ok() is False

    monkeypatch.setattr(
        time_health.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args=["timedatectl"], returncode=1, stdout="", stderr=""
        ),
    )
    assert time_health._query_ntp_sync_ok() is None

    monkeypatch.setattr(
        time_health.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args=["timedatectl"], returncode=0, stdout="unknown\n", stderr=""
        ),
    )
    assert time_health._query_ntp_sync_ok() is None

    def raise_exc(*args: Any, **kwargs: Any) -> Any:
        raise OSError("x")

    monkeypatch.setattr(time_health.subprocess, "run", raise_exc)
    assert time_health._query_ntp_sync_ok() is None


def test_time_health_sets_confirmed_reboot_signal(monkeypatch: Any) -> None:
    monkeypatch.setattr("raspi_sentinel.time_health.time.monotonic", lambda: 160.0)
    monkeypatch.setattr(
        "raspi_sentinel.time_health._fetch_http_date_epoch",
        lambda url, timeout_sec: (1600.0, None),
    )
    monkeypatch.setattr(
        "raspi_sentinel.time_health._query_ntp_sync_ok", lambda timeout_sec=3: False
    )

    state = {
        "clock_prev_wall_time_epoch": 1000.0,
        "clock_prev_monotonic_sec": 100.0,
    }
    result = CheckResult(
        target="clock_target",
        healthy=True,
        failures=[],
        observations={"dns_ok": True, "gateway_ok": True},
    )
    apply_time_health_checks(
        target=_target(
            http_time_probe_url="https://www.google.com",
            clock_anomaly_reboot_consecutive=1,
        ),
        target_state=state,
        result=result,
        now_wall_ts=1000.1,
    )

    assert result.observations["clock_frozen_confirmed"] is True
    assert result.observations["clock_reboot_ready"] is True
    assert result.observations["clock_reason"] == "clock_frozen_confirmed"


def test_time_health_detects_jump_without_freeze(monkeypatch: Any) -> None:
    monkeypatch.setattr("raspi_sentinel.time_health.time.monotonic", lambda: 210.0)
    monkeypatch.setattr("raspi_sentinel.time_health._query_ntp_sync_ok", lambda timeout_sec=3: None)

    state = {
        "clock_prev_wall_time_epoch": 2000.0,
        "clock_prev_monotonic_sec": 200.0,
    }
    result = CheckResult(target="clock_target", healthy=True, failures=[])
    apply_time_health_checks(
        target=_target(check_interval_threshold_sec=5),
        target_state=state,
        result=result,
        now_wall_ts=2100.0,
    )

    assert result.observations["clock_jump_detected"] is True
    assert result.observations["clock_reason"] == "clock_jump"


def test_time_health_http_probe_failed_reason(monkeypatch: Any) -> None:
    monkeypatch.setattr("raspi_sentinel.time_health.time.monotonic", lambda: 10.0)
    monkeypatch.setattr(
        "raspi_sentinel.time_health._fetch_http_date_epoch",
        lambda url, timeout_sec: (None, "network-down"),
    )
    monkeypatch.setattr("raspi_sentinel.time_health._query_ntp_sync_ok", lambda timeout_sec=3: None)

    state: dict[str, Any] = {}
    result = CheckResult(target="clock_target", healthy=True, failures=[])
    apply_time_health_checks(
        target=_target(http_time_probe_url="https://www.google.com"),
        target_state=state,
        result=result,
        now_wall_ts=1000.0,
    )
    assert result.observations["http_probe_ok"] is False
    assert result.observations["clock_reason"] == "http_probe_failed"


def test_time_health_accepts_injected_monotonic_time(monkeypatch: Any) -> None:
    monkeypatch.setattr("raspi_sentinel.time_health._query_ntp_sync_ok", lambda timeout_sec=3: None)

    state = {
        "clock_prev_wall_time_epoch": 1000.0,
        "clock_prev_monotonic_sec": 100.0,
    }
    result = CheckResult(target="clock_target", healthy=True, failures=[])
    apply_time_health_checks(
        target=_target(check_interval_threshold_sec=5),
        target_state=state,
        result=result,
        now_wall_ts=1010.0,
        now_mono_ts=110.0,
    )

    assert result.observations["delta_monotonic_sec"] == 10.0
