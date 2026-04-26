from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from raspi_sentinel.config import (
    AppConfig,
    DependencyCheckConfig,
    DiscordNotifyConfig,
    ExternalStatusCheckConfig,
    GlobalConfig,
    MaintenanceCheckConfig,
    NetworkProbeConfig,
    NotifyConfig,
    StatsCheckConfig,
    TargetConfig,
    TimeHealthCheckConfig,
)

_DEPS_FIELDS = {f.name for f in DependencyCheckConfig.__dataclass_fields__.values()}
_NETWORK_FIELDS = {f.name for f in NetworkProbeConfig.__dataclass_fields__.values()}
_STATS_FIELDS = {f.name for f in StatsCheckConfig.__dataclass_fields__.values()}
_TIME_HEALTH_FIELDS = {f.name for f in TimeHealthCheckConfig.__dataclass_fields__.values()}
_MAINTENANCE_FIELDS = {f.name for f in MaintenanceCheckConfig.__dataclass_fields__.values()}
_EXTERNAL_FIELDS = {f.name for f in ExternalStatusCheckConfig.__dataclass_fields__.values()}


def _default_flat_kwargs() -> dict[str, Any]:
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
    """Build a TargetConfig from flat keyword arguments (backward compatible).

    New tests should prefer explicit grouped fields (`deps`, `network`, `stats`, etc.)
    where practical to reduce reliance on flat compatibility shims.
    """
    flat = _default_flat_kwargs()
    flat.update(overrides)

    deps_kw = {k: flat.pop(k) for k in list(flat) if k in _DEPS_FIELDS}
    net_kw = {k: flat.pop(k) for k in list(flat) if k in _NETWORK_FIELDS}
    stats_kw = {k: flat.pop(k) for k in list(flat) if k in _STATS_FIELDS}
    th_kw = {k: flat.pop(k) for k in list(flat) if k in _TIME_HEALTH_FIELDS}
    maint_kw = {k: flat.pop(k) for k in list(flat) if k in _MAINTENANCE_FIELDS}
    ext_kw = {k: flat.pop(k) for k in list(flat) if k in _EXTERNAL_FIELDS}

    return TargetConfig(
        **flat,
        deps=DependencyCheckConfig(**deps_kw),
        network=NetworkProbeConfig(**net_kw),
        stats=StatsCheckConfig(**stats_kw),
        time_health=TimeHealthCheckConfig(**th_kw),
        maintenance=MaintenanceCheckConfig(**maint_kw),
        external=ExternalStatusCheckConfig(**ext_kw),
    )


def make_global_config(**overrides: Any) -> GlobalConfig:
    base: dict[str, Any] = {
        "state_file": Path("/tmp/raspi-sentinel-test-state.json"),
        "state_durable_file": None,
        "state_durable_fields": (),
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
        "restart_service_timeout_sec": 30,
        "default_command_timeout_sec": 10,
        "loop_interval_sec": 60,
        "storage_require_tmpfs": False,
        "storage_verify_min_free_bytes": 1_048_576,
        "storage_verify_write_bytes": 4096,
        "storage_verify_cooldown_sec": 2,
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
        "retry_interval_sec": 60,
        "retry_backoff_base_sec": 0.5,
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
