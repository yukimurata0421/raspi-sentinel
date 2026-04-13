from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from raspi_sentinel.checks import CheckFailure, CheckResult
from raspi_sentinel.config import GlobalConfig, TargetConfig
from raspi_sentinel.recovery import apply_recovery


def _global() -> GlobalConfig:
    return GlobalConfig(
        state_file=Path("/tmp/raspi-sentinel-test-state.json"),
        state_max_file_bytes=2_000_000,
        state_reboots_max_entries=256,
        state_lock_timeout_sec=5,
        events_file=Path("/tmp/raspi-sentinel-test-events.jsonl"),
        events_max_file_bytes=5_000_000,
        events_backup_generations=3,
        monitor_stats_file=Path("/tmp/raspi-sentinel-test-monitor-stats.json"),
        monitor_stats_interval_sec=30,
        restart_threshold=1,
        reboot_threshold=1,
        restart_cooldown_sec=0,
        reboot_cooldown_sec=0,
        reboot_window_sec=3600,
        max_reboots_in_window=2,
        min_uptime_for_reboot_sec=0,
        default_command_timeout_sec=5,
        loop_interval_sec=30,
    )


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
        "command_use_shell": False,
        "command_timeout_sec": None,
        "dns_check_command": None,
        "dns_check_use_shell": False,
        "dns_server_check_command": None,
        "dns_server_check_use_shell": False,
        "gateway_check_command": None,
        "gateway_check_use_shell": False,
        "link_check_command": None,
        "link_check_use_shell": False,
        "default_route_check_command": None,
        "default_route_check_use_shell": False,
        "internet_ip_check_command": None,
        "internet_ip_check_use_shell": False,
        "wan_vs_target_check_command": None,
        "wan_vs_target_check_use_shell": False,
        "network_probe_enabled": False,
        "network_interface": None,
        "gateway_probe_timeout_sec": 2,
        "internet_ip_targets": ["1.1.1.1", "8.8.8.8"],
        "dns_query_target": None,
        "http_probe_target": None,
        "consecutive_failure_thresholds": {"degraded": 2, "failed": 6},
        "latency_thresholds_ms": {},
        "packet_loss_thresholds_pct": {},
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
        "restart_threshold": 1,
        "reboot_threshold": 5,
    }
    base.update(overrides)
    return TargetConfig(**base)


def test_systemd_failure_triggers_restart(monkeypatch: Any) -> None:
    calls: list[list[str]] = []

    def fake_run(cmd: list[str], **_: Any) -> Any:
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr("raspi_sentinel.recovery.subprocess.run", fake_run)

    result = CheckResult(
        target="demo",
        healthy=False,
        failures=[CheckFailure("service_active", "service not active")],
    )
    state: dict[str, Any] = {}
    outcome = apply_recovery(
        target=_target(
            services=["demo.service"],
            service_active=True,
            restart_threshold=1,
            reboot_threshold=5,
        ),
        check_result=result,
        global_config=_global(),
        state=state,
        dry_run=False,
    )
    assert outcome.action == "restart"
    assert not outcome.requested_reboot
    assert calls and calls[0] == ["systemctl", "restart", "demo.service"]


def test_dns_only_failure_does_not_reboot_even_when_threshold_reached() -> None:
    result = CheckResult(
        target="demo",
        healthy=False,
        failures=[CheckFailure("dependency_dns", "dns check failed")],
    )
    state: dict[str, Any] = {}
    outcome = apply_recovery(
        target=_target(restart_threshold=1, reboot_threshold=1),
        check_result=result,
        global_config=_global(),
        state=state,
        dry_run=True,
    )
    assert outcome.action == "warn"
    assert not outcome.requested_reboot
    assert state.get("reboots") in (None, [])


def test_gateway_failure_can_request_reboot(monkeypatch: Any) -> None:
    monkeypatch.setattr("raspi_sentinel.recovery._get_uptime_sec", lambda: 999999.0)
    result = CheckResult(
        target="demo",
        healthy=False,
        failures=[CheckFailure("dependency_gateway", "gateway failed")],
        observations={"policy_status": "failed"},
    )
    state: dict[str, Any] = {}
    outcome = apply_recovery(
        target=_target(restart_threshold=1, reboot_threshold=1),
        check_result=result,
        global_config=_global(),
        state=state,
        dry_run=True,
    )
    assert outcome.action == "reboot"
    assert outcome.requested_reboot


def test_recovery_reboot_requires_policy_failed_for_network_uplink(monkeypatch: Any) -> None:
    monkeypatch.setattr("raspi_sentinel.recovery._get_uptime_sec", lambda: 999999.0)
    result = CheckResult(
        target="network_uplink",
        healthy=False,
        failures=[CheckFailure("dependency_gateway", "gateway failed")],
        observations={"policy_status": "degraded"},
    )
    state: dict[str, Any] = {}
    outcome = apply_recovery(
        target=_target(name="network_uplink", restart_threshold=1, reboot_threshold=1),
        check_result=result,
        global_config=_global(),
        state=state,
        dry_run=True,
    )
    assert outcome.action == "warn"
    assert not outcome.requested_reboot

    failed_result = CheckResult(
        target="network_uplink",
        healthy=False,
        failures=[CheckFailure("dependency_gateway", "gateway failed")],
        observations={"policy_status": "failed"},
    )
    outcome_failed = apply_recovery(
        target=_target(name="network_uplink", restart_threshold=1, reboot_threshold=1),
        check_result=failed_result,
        global_config=_global(),
        state={},
        dry_run=True,
    )
    assert outcome_failed.action == "reboot"
    assert outcome_failed.requested_reboot
