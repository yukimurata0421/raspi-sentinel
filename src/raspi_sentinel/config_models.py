from __future__ import annotations

import inspect
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class GlobalConfig:
    state_file: Path
    state_durable_file: Path | None
    state_durable_fields: tuple[str, ...]
    state_max_file_bytes: int
    state_reboots_max_entries: int
    state_lock_timeout_sec: int
    events_file: Path
    events_max_file_bytes: int
    events_backup_generations: int
    monitor_stats_file: Path
    monitor_stats_interval_sec: int
    restart_threshold: int
    reboot_threshold: int
    restart_cooldown_sec: int
    reboot_cooldown_sec: int
    reboot_window_sec: int
    max_reboots_in_window: int
    min_uptime_for_reboot_sec: int
    default_command_timeout_sec: int
    loop_interval_sec: int
    storage_require_tmpfs: bool
    storage_verify_min_free_bytes: int
    storage_verify_write_bytes: int
    storage_verify_cooldown_sec: int


@dataclass(slots=True)
class DependencyCheckConfig:
    dns_check_command: str | None
    dns_check_use_shell: bool
    dns_server_check_command: str | None
    dns_server_check_use_shell: bool
    gateway_check_command: str | None
    gateway_check_use_shell: bool
    link_check_command: str | None
    link_check_use_shell: bool
    default_route_check_command: str | None
    default_route_check_use_shell: bool
    internet_ip_check_command: str | None
    internet_ip_check_use_shell: bool
    wan_vs_target_check_command: str | None
    wan_vs_target_check_use_shell: bool
    dependency_check_timeout_sec: int | None


@dataclass(slots=True)
class NetworkProbeConfig:
    network_probe_enabled: bool
    network_interface: str | None
    gateway_probe_timeout_sec: int
    internet_ip_targets: list[str]
    dns_query_target: str | None
    http_probe_target: str | None
    consecutive_failure_thresholds: dict[str, int]
    latency_thresholds_ms: dict[str, float]
    packet_loss_thresholds_pct: dict[str, float]


@dataclass(slots=True)
class StatsCheckConfig:
    stats_file: Path | None
    stats_updated_max_age_sec: int | None
    stats_last_input_max_age_sec: int | None
    stats_last_success_max_age_sec: int | None
    stats_records_stall_cycles: int | None


@dataclass(slots=True)
class TimeHealthCheckConfig:
    time_health_enabled: bool
    check_interval_threshold_sec: int
    wall_clock_freeze_min_monotonic_sec: int
    wall_clock_freeze_max_wall_advance_sec: int
    wall_clock_drift_threshold_sec: int
    http_time_probe_url: str | None
    http_time_probe_timeout_sec: int
    clock_skew_threshold_sec: int
    clock_anomaly_reboot_consecutive: int


@dataclass(slots=True)
class MaintenanceCheckConfig:
    maintenance_mode_command: str | None
    maintenance_mode_use_shell: bool
    maintenance_mode_timeout_sec: int | None
    maintenance_grace_sec: int | None


@dataclass(slots=True)
class ExternalStatusCheckConfig:
    external_status_file: Path | None = None
    external_status_updated_max_age_sec: int | None = None
    external_status_last_progress_max_age_sec: int | None = None
    external_status_last_success_max_age_sec: int | None = None
    external_status_startup_grace_sec: int = 120
    external_status_unhealthy_values: tuple[str, ...] = ("failed", "unhealthy")


_SUB_CONFIG_ATTRS = ("deps", "network", "stats", "time_health", "maintenance", "external")
_DEPRECATED_ATTR_WARNED: set[str] = set()
_DEPRECATED_ATTR_REMOVAL_VERSION = "1.0.0"


def _reset_deprecated_attr_warnings_for_tests() -> None:
    """Test-only helper for resetting deprecation warning state."""
    _DEPRECATED_ATTR_WARNED.clear()


@dataclass
class TargetConfig:
    """Per-target configuration.

    Fields are logically grouped into sub-dataclasses accessible via
    ``deps``, ``network``, ``stats``, ``time_health``, ``maintenance``,
    and ``external``.  For backward compatibility every sub-field is also
    reachable as a flat attribute (e.g. ``target.dns_check_command``
    delegates to ``target.deps.dns_check_command``).

    The flat-attribute shim is deprecated and planned to be removed in
    v1.0.0.
    """

    name: str
    services: list[str]
    service_active: bool
    heartbeat_file: Path | None
    heartbeat_max_age_sec: int | None
    output_file: Path | None
    output_max_age_sec: int | None
    command: str | None
    command_use_shell: bool
    command_timeout_sec: int | None
    restart_threshold: int | None
    reboot_threshold: int | None
    deps: DependencyCheckConfig
    network: NetworkProbeConfig
    stats: StatsCheckConfig
    time_health: TimeHealthCheckConfig
    maintenance: MaintenanceCheckConfig
    external: ExternalStatusCheckConfig

    def __getattr__(self, name: str) -> Any:
        for attr in _SUB_CONFIG_ATTRS:
            try:
                sub = object.__getattribute__(self, attr)
            except AttributeError:
                continue
            try:
                value = getattr(sub, name)
                if name in _DEPRECATED_ATTR_WARNED:
                    return value
                current = inspect.currentframe()
                caller = current.f_back if current is not None else None
                caller_mod = caller.f_globals.get("__name__", "") if caller is not None else ""
                should_warn = not str(caller_mod).startswith("raspi_sentinel.")
                if should_warn and name not in _DEPRECATED_ATTR_WARNED:
                    warnings.warn(
                        (
                            f"TargetConfig.{name} is deprecated; "
                            f"use TargetConfig.{attr}.{name} instead "
                            f"(planned removal in v{_DEPRECATED_ATTR_REMOVAL_VERSION})"
                        ),
                        DeprecationWarning,
                        stacklevel=2,
                    )
                    _DEPRECATED_ATTR_WARNED.add(name)
                return value
            except AttributeError:
                continue
        raise AttributeError(f"'{type(self).__name__}' object has no attribute '{name}'")


@dataclass(slots=True)
class AppConfig:
    global_config: GlobalConfig
    notify_config: "NotifyConfig"
    targets: list[TargetConfig]


@dataclass(slots=True)
class DiscordNotifyConfig:
    enabled: bool
    webhook_url: str | None
    username: str
    timeout_sec: int
    followup_delay_sec: int
    retry_interval_sec: int
    heartbeat_interval_sec: int
    notify_on_recovery: bool


@dataclass(slots=True)
class NotifyConfig:
    discord: DiscordNotifyConfig
