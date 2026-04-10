from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from raspi_sentinel import recovery
from raspi_sentinel.checks import CheckFailure, CheckResult
from raspi_sentinel.config import GlobalConfig, TargetConfig


def _global(**overrides: Any) -> GlobalConfig:
    base = {
        "state_file": Path("/tmp/state.json"),
        "state_max_file_bytes": 2_000_000,
        "state_reboots_max_entries": 256,
        "state_lock_timeout_sec": 5,
        "events_file": Path("/tmp/events.jsonl"),
        "events_max_file_bytes": 5_000_000,
        "events_backup_generations": 3,
        "monitor_stats_file": Path("/tmp/stats.json"),
        "monitor_stats_interval_sec": 30,
        "restart_threshold": 2,
        "reboot_threshold": 3,
        "restart_cooldown_sec": 30,
        "reboot_cooldown_sec": 30,
        "reboot_window_sec": 300,
        "max_reboots_in_window": 2,
        "min_uptime_for_reboot_sec": 60,
        "default_command_timeout_sec": 5,
        "loop_interval_sec": 30,
    }
    base.update(overrides)
    return GlobalConfig(**base)


def _target(**overrides: Any) -> TargetConfig:
    base = {
        "name": "demo",
        "services": ["demo.service"],
        "service_active": True,
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
        "maintenance_mode_use_shell": False,
        "maintenance_mode_timeout_sec": None,
        "maintenance_grace_sec": None,
        "restart_threshold": 2,
        "reboot_threshold": 3,
    }
    base.update(overrides)
    return TargetConfig(**base)


def test_can_reboot_guard_paths(monkeypatch: Any) -> None:
    monkeypatch.setattr(recovery, "_get_uptime_sec", lambda: 10.0)
    ok, reason = recovery._can_reboot(_global(min_uptime_for_reboot_sec=60), {}, 100.0)
    assert not ok and "uptime guard" in reason

    monkeypatch.setattr(recovery, "_get_uptime_sec", lambda: 1000.0)
    state = {"reboots": [{"ts": 95.0}]}
    ok, reason = recovery._can_reboot(_global(reboot_cooldown_sec=20), state, 100.0)
    assert not ok and "cooldown" in reason

    state = {"reboots": [{"ts": 80.0}, {"ts": 90.0}]}
    ok, reason = recovery._can_reboot(
        _global(reboot_cooldown_sec=0, max_reboots_in_window=2), state, 100.0
    )
    assert not ok and "window cap" in reason

    state = {"reboots": [{"ts": 0.0}]}
    ok, reason = recovery._can_reboot(_global(reboot_window_sec=10), state, 100.0)
    assert ok and reason == "allowed"
    assert state["reboots"] == []


def test_restart_services_branches(monkeypatch: Any) -> None:
    assert not recovery._restart_services([], dry_run=True)
    assert recovery._restart_services(["svc"], dry_run=True)

    def timeout_run(*_: Any, **__: Any) -> Any:
        raise subprocess.TimeoutExpired(cmd="systemctl", timeout=30)

    monkeypatch.setattr(recovery.subprocess, "run", timeout_run)
    assert not recovery._restart_services(["svc"], dry_run=False)

    def os_run(*_: Any, **__: Any) -> Any:
        raise OSError("no systemctl")

    monkeypatch.setattr(recovery.subprocess, "run", os_run)
    assert not recovery._restart_services(["svc"], dry_run=False)

    def fail_run(*_: Any, **__: Any) -> Any:
        return subprocess.CompletedProcess(
            args=["systemctl"], returncode=1, stdout="", stderr="failed"
        )

    monkeypatch.setattr(recovery.subprocess, "run", fail_run)
    assert not recovery._restart_services(["svc"], dry_run=False)

    def ok_run(*_: Any, **__: Any) -> Any:
        return subprocess.CompletedProcess(args=["systemctl"], returncode=0, stdout="", stderr="")

    monkeypatch.setattr(recovery.subprocess, "run", ok_run)
    assert recovery._restart_services(["svc"], dry_run=False)


def test_trigger_reboot_branches(monkeypatch: Any) -> None:
    assert recovery._trigger_reboot(dry_run=True, reason="x")

    def timeout_run(*_: Any, **__: Any) -> Any:
        raise subprocess.TimeoutExpired(cmd="reboot", timeout=15)

    monkeypatch.setattr(recovery.subprocess, "run", timeout_run)
    assert not recovery._trigger_reboot(dry_run=False, reason="x")

    def os_run(*_: Any, **__: Any) -> Any:
        raise OSError("boom")

    monkeypatch.setattr(recovery.subprocess, "run", os_run)
    assert not recovery._trigger_reboot(dry_run=False, reason="x")

    def fail_run(*_: Any, **__: Any) -> Any:
        return subprocess.CompletedProcess(
            args=["systemctl"], returncode=1, stdout="", stderr="failed"
        )

    monkeypatch.setattr(recovery.subprocess, "run", fail_run)
    assert not recovery._trigger_reboot(dry_run=False, reason="x")

    def ok_run(*_: Any, **__: Any) -> Any:
        return subprocess.CompletedProcess(args=["systemctl"], returncode=0, stdout="", stderr="")

    monkeypatch.setattr(recovery.subprocess, "run", ok_run)
    assert recovery._trigger_reboot(dry_run=False, reason="x")


def test_apply_recovery_healthy_path_resets_counter() -> None:
    state: dict[str, Any] = {"targets": {"demo": {"consecutive_failures": 3}}}
    outcome = recovery.apply_recovery(
        target=_target(),
        check_result=CheckResult(target="demo", healthy=True, failures=[]),
        global_config=_global(),
        state=state,
        dry_run=True,
    )
    assert outcome.action == "none"
    assert state["targets"]["demo"]["consecutive_failures"] == 0


def test_apply_recovery_injected_now_ts_used_for_last_healthy() -> None:
    state: dict[str, Any] = {}
    outcome = recovery.apply_recovery(
        target=_target(),
        check_result=CheckResult(target="demo", healthy=True, failures=[]),
        global_config=_global(),
        state=state,
        dry_run=True,
        now_ts=42.0,
    )
    assert outcome.action == "none"
    assert state["targets"]["demo"]["last_healthy_ts"] == 42.0


def test_apply_recovery_restart_cooldown_suppresses_repeat(monkeypatch: Any) -> None:
    state: dict[str, Any] = {
        "targets": {
            "demo": {
                "consecutive_failures": 1,
                "last_action": "restart",
                "last_action_ts": 95.0,
            }
        }
    }
    outcome = recovery.apply_recovery(
        target=_target(restart_threshold=2, reboot_threshold=9),
        check_result=CheckResult(
            target="demo",
            healthy=False,
            failures=[CheckFailure("service_active", "down")],
        ),
        global_config=_global(restart_cooldown_sec=10),
        state=state,
        dry_run=True,
        now_ts=100.0,
    )
    assert outcome.action == "warn"
    assert not outcome.requested_reboot


def test_apply_recovery_reboot_failure_falls_back_to_restart(monkeypatch: Any) -> None:
    monkeypatch.setattr(recovery, "_get_uptime_sec", lambda: 1000.0)
    monkeypatch.setattr(recovery, "_trigger_reboot", lambda dry_run, reason: False)
    monkeypatch.setattr(recovery, "_restart_services", lambda services, dry_run: True)
    state: dict[str, Any] = {}
    outcome = recovery.apply_recovery(
        target=_target(restart_threshold=1, reboot_threshold=1),
        check_result=CheckResult(
            target="demo",
            healthy=False,
            failures=[CheckFailure("dependency_gateway", "gw")],
        ),
        global_config=_global(
            min_uptime_for_reboot_sec=0, restart_cooldown_sec=0, reboot_cooldown_sec=0
        ),
        state=state,
        dry_run=True,
    )
    assert outcome.action == "restart"
    assert not outcome.requested_reboot


def test_apply_recovery_clock_only_blocks_reboot_without_ready() -> None:
    state: dict[str, Any] = {}
    outcome = recovery.apply_recovery(
        target=_target(restart_threshold=1, reboot_threshold=1),
        check_result=CheckResult(
            target="demo",
            healthy=False,
            failures=[CheckFailure("semantic_clock_skew", "clock skew")],
            observations={"clock_reboot_ready": False},
        ),
        global_config=_global(
            min_uptime_for_reboot_sec=0, reboot_cooldown_sec=0, restart_cooldown_sec=0
        ),
        state=state,
        dry_run=True,
    )
    assert outcome.action == "restart"
    assert not outcome.requested_reboot


def test_apply_recovery_clock_only_allows_reboot_when_ready(monkeypatch: Any) -> None:
    monkeypatch.setattr(recovery, "_get_uptime_sec", lambda: 1000.0)
    state: dict[str, Any] = {}
    outcome = recovery.apply_recovery(
        target=_target(restart_threshold=1, reboot_threshold=1),
        check_result=CheckResult(
            target="demo",
            healthy=False,
            failures=[CheckFailure("semantic_clock_frozen", "clock frozen")],
            observations={"clock_reboot_ready": True},
        ),
        global_config=_global(
            min_uptime_for_reboot_sec=0, reboot_cooldown_sec=0, restart_cooldown_sec=0
        ),
        state=state,
        dry_run=True,
    )
    assert outcome.action == "reboot"
    assert outcome.requested_reboot


def test_apply_recovery_reboots_on_confirmed_clock_without_failures(
    monkeypatch: Any,
) -> None:
    monkeypatch.setattr(recovery, "_get_uptime_sec", lambda: 1000.0)
    state: dict[str, Any] = {}
    outcome = recovery.apply_recovery(
        target=_target(restart_threshold=9, reboot_threshold=9),
        check_result=CheckResult(
            target="demo",
            healthy=True,
            failures=[],
            observations={
                "clock_frozen_confirmed": True,
                "policy_reason": "clock_frozen_confirmed",
            },
        ),
        global_config=_global(
            min_uptime_for_reboot_sec=0, reboot_cooldown_sec=0, restart_cooldown_sec=0
        ),
        state=state,
        dry_run=True,
    )
    assert outcome.action == "reboot"
    assert outcome.requested_reboot
