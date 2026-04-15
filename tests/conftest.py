from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from raspi_sentinel.config import (
    AppConfig,
    DiscordNotifyConfig,
    GlobalConfig,
    NotifyConfig,
    TargetConfig,
)


def _default_target_kwargs() -> dict[str, Any]:
    return {
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
        "restart_threshold": None,
        "reboot_threshold": None,
    }


def make_target(**overrides: Any) -> TargetConfig:
    base = _default_target_kwargs()
    base.update(overrides)
    return TargetConfig(**base)


def make_global_config(**overrides: Any) -> GlobalConfig:
    base: dict[str, Any] = {
        "state_file": Path("/tmp/raspi-sentinel-test-state.json"),
        "state_max_file_bytes": 2_000_000,
        "state_reboots_max_entries": 256,
        "state_lock_timeout_sec": 5,
        "events_file": Path("/tmp/raspi-sentinel-test-events.jsonl"),
        "events_max_file_bytes": 5_000_000,
        "events_backup_generations": 3,
        "monitor_stats_file": Path("/tmp/raspi-sentinel-test-monitor-stats.json"),
        "monitor_stats_interval_sec": 30,
        "restart_threshold": 3,
        "reboot_threshold": 6,
        "restart_cooldown_sec": 120,
        "reboot_cooldown_sec": 1800,
        "reboot_window_sec": 21600,
        "max_reboots_in_window": 2,
        "min_uptime_for_reboot_sec": 600,
        "default_command_timeout_sec": 10,
        "loop_interval_sec": 60,
    }
    base.update(overrides)
    return GlobalConfig(**base)


def make_discord_config(**overrides: Any) -> DiscordNotifyConfig:
    base: dict[str, Any] = {
        "enabled": False,
        "webhook_url": None,
        "username": "raspi-sentinel",
        "timeout_sec": 5,
        "followup_delay_sec": 300,
        "heartbeat_interval_sec": 300,
        "notify_on_recovery": True,
    }
    base.update(overrides)
    return DiscordNotifyConfig(**base)


def make_app_config(
    *,
    global_overrides: dict[str, Any] | None = None,
    discord_overrides: dict[str, Any] | None = None,
    targets: list[TargetConfig] | None = None,
) -> AppConfig:
    return AppConfig(
        global_config=make_global_config(**(global_overrides or {})),
        notify_config=NotifyConfig(discord=make_discord_config(**(discord_overrides or {}))),
        targets=targets or [make_target()],
    )


@pytest.fixture()
def events_file(tmp_path: Path) -> Path:
    return tmp_path / "events.jsonl"
