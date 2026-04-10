from __future__ import annotations

import logging
import stat
import tomllib
from dataclasses import dataclass
from pathlib import Path

LOG = logging.getLogger(__name__)


@dataclass(slots=True)
class GlobalConfig:
    state_file: Path
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


@dataclass(slots=True)
class TargetConfig:
    name: str
    services: list[str]
    service_active: bool
    heartbeat_file: Path | None
    heartbeat_max_age_sec: int | None
    output_file: Path | None
    output_max_age_sec: int | None
    command: str | None
    command_timeout_sec: int | None
    dns_check_command: str | None
    gateway_check_command: str | None
    dependency_check_timeout_sec: int | None
    stats_file: Path | None
    stats_updated_max_age_sec: int | None
    stats_last_input_max_age_sec: int | None
    stats_last_success_max_age_sec: int | None
    stats_records_stall_cycles: int | None
    time_health_enabled: bool
    check_interval_threshold_sec: int
    wall_clock_freeze_min_monotonic_sec: int
    wall_clock_freeze_max_wall_advance_sec: int
    wall_clock_drift_threshold_sec: int
    http_time_probe_url: str | None
    http_time_probe_timeout_sec: int
    clock_skew_threshold_sec: int
    clock_anomaly_reboot_consecutive: int
    maintenance_mode_command: str | None
    maintenance_mode_timeout_sec: int | None
    maintenance_grace_sec: int | None
    restart_threshold: int | None
    reboot_threshold: int | None


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
    heartbeat_interval_sec: int


@dataclass(slots=True)
class NotifyConfig:
    discord: DiscordNotifyConfig


def _require_int(data: dict, key: str, default: int | None = None) -> int:
    value = data.get(key, default)
    if not isinstance(value, int):
        raise ValueError(f"'{key}' must be an integer")
    return value


def _optional_int(data: dict, key: str) -> int | None:
    value = data.get(key)
    if value is None:
        return None
    if not isinstance(value, int):
        raise ValueError(f"'{key}' must be an integer")
    return value


def _optional_str(data: dict, key: str) -> str | None:
    value = data.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"'{key}' must be a non-empty string when set")
    return value


def _optional_path(data: dict, key: str) -> Path | None:
    value = _optional_str(data, key)
    return Path(value) if value else None


def _optional_bool(data: dict, key: str, default: bool) -> bool:
    value = data.get(key, default)
    if not isinstance(value, bool):
        raise ValueError(f"'{key}' must be boolean")
    return value


def _validate_target_rules(target: TargetConfig) -> None:
    has_heartbeat = target.heartbeat_file is not None or target.heartbeat_max_age_sec is not None
    if has_heartbeat and (target.heartbeat_file is None or target.heartbeat_max_age_sec is None):
        raise ValueError(
            f"target '{target.name}': heartbeat_file and heartbeat_max_age_sec must be set together"
        )

    has_output = target.output_file is not None or target.output_max_age_sec is not None
    if has_output and (target.output_file is None or target.output_max_age_sec is None):
        raise ValueError(
            f"target '{target.name}': output_file and output_max_age_sec must be set together"
        )

    if target.heartbeat_max_age_sec is not None and target.heartbeat_max_age_sec <= 0:
        raise ValueError(f"target '{target.name}': heartbeat_max_age_sec must be > 0")

    if target.output_max_age_sec is not None and target.output_max_age_sec <= 0:
        raise ValueError(f"target '{target.name}': output_max_age_sec must be > 0")

    if target.command_timeout_sec is not None and target.command_timeout_sec <= 0:
        raise ValueError(f"target '{target.name}': command_timeout_sec must be > 0")

    if target.dependency_check_timeout_sec is not None and target.dependency_check_timeout_sec <= 0:
        raise ValueError(f"target '{target.name}': dependency_check_timeout_sec must be > 0")

    if target.maintenance_mode_timeout_sec is not None and target.maintenance_mode_timeout_sec <= 0:
        raise ValueError(f"target '{target.name}': maintenance_mode_timeout_sec must be > 0")

    if target.maintenance_grace_sec is not None and target.maintenance_grace_sec < 0:
        raise ValueError(f"target '{target.name}': maintenance_grace_sec must be >= 0")

    if target.restart_threshold is not None and target.restart_threshold <= 0:
        raise ValueError(f"target '{target.name}': restart_threshold must be > 0")

    if target.reboot_threshold is not None and target.reboot_threshold <= 0:
        raise ValueError(f"target '{target.name}': reboot_threshold must be > 0")

    if target.reboot_threshold is not None and target.restart_threshold is not None:
        if target.reboot_threshold < target.restart_threshold:
            raise ValueError(
                f"target '{target.name}': reboot_threshold must be >= restart_threshold"
            )

    if target.stats_updated_max_age_sec is not None and target.stats_updated_max_age_sec <= 0:
        raise ValueError(f"target '{target.name}': stats_updated_max_age_sec must be > 0")

    if target.stats_last_input_max_age_sec is not None and target.stats_last_input_max_age_sec <= 0:
        raise ValueError(f"target '{target.name}': stats_last_input_max_age_sec must be > 0")

    if (
        target.stats_last_success_max_age_sec is not None
        and target.stats_last_success_max_age_sec <= 0
    ):
        raise ValueError(f"target '{target.name}': stats_last_success_max_age_sec must be > 0")

    if target.stats_records_stall_cycles is not None and target.stats_records_stall_cycles <= 0:
        raise ValueError(f"target '{target.name}': stats_records_stall_cycles must be > 0")

    if target.service_active and not target.services:
        raise ValueError(
            f"target '{target.name}': when service_active=true, "
            "services must list at least one unit"
        )

    if target.wall_clock_freeze_min_monotonic_sec <= 0:
        raise ValueError(f"target '{target.name}': wall_clock_freeze_min_monotonic_sec must be > 0")

    if target.check_interval_threshold_sec <= 0:
        raise ValueError(f"target '{target.name}': check_interval_threshold_sec must be > 0")

    if target.wall_clock_freeze_max_wall_advance_sec < 0:
        raise ValueError(
            f"target '{target.name}': wall_clock_freeze_max_wall_advance_sec must be >= 0"
        )

    if target.wall_clock_drift_threshold_sec <= 0:
        raise ValueError(f"target '{target.name}': wall_clock_drift_threshold_sec must be > 0")

    if target.http_time_probe_timeout_sec <= 0:
        raise ValueError(f"target '{target.name}': http_time_probe_timeout_sec must be > 0")

    if target.clock_skew_threshold_sec <= 0:
        raise ValueError(f"target '{target.name}': clock_skew_threshold_sec must be > 0")

    if target.clock_anomaly_reboot_consecutive <= 0:
        raise ValueError(f"target '{target.name}': clock_anomaly_reboot_consecutive must be > 0")

    has_stats_rule = any(
        [
            target.stats_updated_max_age_sec is not None,
            target.stats_last_input_max_age_sec is not None,
            target.stats_last_success_max_age_sec is not None,
            target.stats_records_stall_cycles is not None,
        ]
    )
    if has_stats_rule and target.stats_file is None:
        raise ValueError(
            f"target '{target.name}': stats_file is required when stats_* checks are configured"
        )

    has_rule = any(
        [
            target.service_active,
            target.heartbeat_file is not None,
            target.output_file is not None,
            target.command is not None,
            target.stats_file is not None,
            target.dns_check_command is not None,
            target.gateway_check_command is not None,
            target.time_health_enabled,
        ]
    )
    if not has_rule:
        raise ValueError(
            f"target '{target.name}': at least one rule is required "
            "(service_active, heartbeat, output, command, stats_file, "
            "dns_check_command, gateway_check_command, time_health_enabled)"
        )


def _warn_config_permissions(path: Path) -> None:
    try:
        st = path.stat()
    except OSError:
        return
    mode = stat.S_IMODE(st.st_mode)
    if mode & (stat.S_IWGRP | stat.S_IWOTH):
        LOG.warning(
            "config file %s is group/world-writable (mode=%04o); use a trusted path and chmod go-w",
            path,
            mode,
        )


def load_config(path: Path) -> AppConfig:
    _warn_config_permissions(path)
    with path.open("rb") as fh:
        raw = tomllib.load(fh)

    global_raw = raw.get("global", {})
    if not isinstance(global_raw, dict):
        raise ValueError("[global] must be a table")

    global_config = GlobalConfig(
        state_file=Path(global_raw.get("state_file", "/var/lib/raspi-sentinel/state.json")),
        state_max_file_bytes=_require_int(global_raw, "state_max_file_bytes", 2_000_000),
        state_reboots_max_entries=_require_int(global_raw, "state_reboots_max_entries", 256),
        state_lock_timeout_sec=_require_int(global_raw, "state_lock_timeout_sec", 5),
        events_file=Path(global_raw.get("events_file", "/var/lib/raspi-sentinel/events.jsonl")),
        events_max_file_bytes=_require_int(global_raw, "events_max_file_bytes", 5_000_000),
        events_backup_generations=_require_int(global_raw, "events_backup_generations", 3),
        monitor_stats_file=Path(
            global_raw.get("monitor_stats_file", "/var/lib/raspi-sentinel/stats.json")
        ),
        monitor_stats_interval_sec=_require_int(global_raw, "monitor_stats_interval_sec", 30),
        restart_threshold=_require_int(global_raw, "restart_threshold", 3),
        reboot_threshold=_require_int(global_raw, "reboot_threshold", 6),
        restart_cooldown_sec=_require_int(global_raw, "restart_cooldown_sec", 120),
        reboot_cooldown_sec=_require_int(global_raw, "reboot_cooldown_sec", 1800),
        reboot_window_sec=_require_int(global_raw, "reboot_window_sec", 21600),
        max_reboots_in_window=_require_int(global_raw, "max_reboots_in_window", 2),
        min_uptime_for_reboot_sec=_require_int(global_raw, "min_uptime_for_reboot_sec", 600),
        default_command_timeout_sec=_require_int(global_raw, "default_command_timeout_sec", 10),
        loop_interval_sec=_require_int(global_raw, "loop_interval_sec", 60),
    )

    if global_config.restart_threshold <= 0:
        raise ValueError("global restart_threshold must be > 0")
    if global_config.reboot_threshold < global_config.restart_threshold:
        raise ValueError("global reboot_threshold must be >= restart_threshold")
    if global_config.reboot_cooldown_sec < 0 or global_config.restart_cooldown_sec < 0:
        raise ValueError("global cooldown values must be >= 0")
    if global_config.reboot_window_sec <= 0:
        raise ValueError("global reboot_window_sec must be > 0")
    if global_config.max_reboots_in_window <= 0:
        raise ValueError("global max_reboots_in_window must be > 0")
    if global_config.default_command_timeout_sec <= 0:
        raise ValueError("global default_command_timeout_sec must be > 0")
    if global_config.loop_interval_sec <= 0:
        raise ValueError("global loop_interval_sec must be > 0")
    if global_config.monitor_stats_interval_sec <= 0:
        raise ValueError("global monitor_stats_interval_sec must be > 0")
    if global_config.events_max_file_bytes < 0:
        raise ValueError(
            "global events_max_file_bytes must be >= 0 (0 disables events.jsonl size rotation)"
        )
    if global_config.events_backup_generations <= 0:
        raise ValueError("global events_backup_generations must be > 0")
    if global_config.state_max_file_bytes < 0:
        raise ValueError(
            "global state_max_file_bytes must be >= 0 (0 disables state.json size guard)"
        )
    if global_config.state_reboots_max_entries <= 0:
        raise ValueError("global state_reboots_max_entries must be > 0")
    if global_config.state_lock_timeout_sec <= 0:
        raise ValueError("global state_lock_timeout_sec must be > 0")

    notify_raw = raw.get("notify", {})
    if not isinstance(notify_raw, dict):
        raise ValueError("[notify] must be a table")

    discord_raw = notify_raw.get("discord", {})
    if not isinstance(discord_raw, dict):
        raise ValueError("[notify.discord] must be a table")

    discord_enabled = discord_raw.get("enabled", False)
    if not isinstance(discord_enabled, bool):
        raise ValueError("[notify.discord].enabled must be boolean")

    discord_webhook = _optional_str(discord_raw, "webhook_url")
    discord_username = discord_raw.get("username", "raspi-sentinel")
    if not isinstance(discord_username, str) or not discord_username.strip():
        raise ValueError("[notify.discord].username must be a non-empty string")

    discord_config = DiscordNotifyConfig(
        enabled=discord_enabled,
        webhook_url=discord_webhook,
        username=discord_username,
        timeout_sec=_require_int(discord_raw, "timeout_sec", 5),
        followup_delay_sec=_require_int(discord_raw, "followup_delay_sec", 300),
        heartbeat_interval_sec=_require_int(discord_raw, "heartbeat_interval_sec", 300),
    )

    if discord_config.timeout_sec <= 0:
        raise ValueError("[notify.discord].timeout_sec must be > 0")
    if discord_config.followup_delay_sec <= 0:
        raise ValueError("[notify.discord].followup_delay_sec must be > 0")
    if discord_config.heartbeat_interval_sec < 0:
        raise ValueError("[notify.discord].heartbeat_interval_sec must be >= 0")
    if discord_config.enabled and not discord_config.webhook_url:
        raise ValueError("[notify.discord].webhook_url is required when enabled=true")

    targets_raw = raw.get("targets")
    if not isinstance(targets_raw, list) or not targets_raw:
        raise ValueError("At least one [[targets]] section is required")

    targets: list[TargetConfig] = []
    seen_names: set[str] = set()
    for item in targets_raw:
        if not isinstance(item, dict):
            raise ValueError("Each [[targets]] entry must be a table")

        name = item.get("name")
        if not isinstance(name, str) or not name.strip():
            raise ValueError("target name must be a non-empty string")
        if name in seen_names:
            raise ValueError(f"duplicate target name: {name}")
        seen_names.add(name)

        services_raw = item.get("services", [])
        if not isinstance(services_raw, list) or any(not isinstance(s, str) for s in services_raw):
            raise ValueError(f"target '{name}': services must be an array of strings")

        service_active = item.get("service_active", True)
        if not isinstance(service_active, bool):
            raise ValueError(f"target '{name}': service_active must be boolean")

        target = TargetConfig(
            name=name,
            services=services_raw,
            service_active=service_active,
            heartbeat_file=_optional_path(item, "heartbeat_file"),
            heartbeat_max_age_sec=_optional_int(item, "heartbeat_max_age_sec"),
            output_file=_optional_path(item, "output_file"),
            output_max_age_sec=_optional_int(item, "output_max_age_sec"),
            command=_optional_str(item, "command"),
            command_timeout_sec=_optional_int(item, "command_timeout_sec"),
            dns_check_command=_optional_str(item, "dns_check_command"),
            gateway_check_command=_optional_str(item, "gateway_check_command"),
            dependency_check_timeout_sec=_optional_int(item, "dependency_check_timeout_sec"),
            stats_file=_optional_path(item, "stats_file"),
            stats_updated_max_age_sec=_optional_int(item, "stats_updated_max_age_sec"),
            stats_last_input_max_age_sec=_optional_int(item, "stats_last_input_max_age_sec"),
            stats_last_success_max_age_sec=_optional_int(item, "stats_last_success_max_age_sec"),
            stats_records_stall_cycles=_optional_int(item, "stats_records_stall_cycles"),
            time_health_enabled=_optional_bool(item, "time_health_enabled", False),
            check_interval_threshold_sec=_require_int(item, "check_interval_threshold_sec", 30),
            wall_clock_freeze_min_monotonic_sec=_require_int(
                item,
                "wall_clock_freeze_min_monotonic_sec",
                25,
            ),
            wall_clock_freeze_max_wall_advance_sec=_require_int(
                item,
                "wall_clock_freeze_max_wall_advance_sec",
                1,
            ),
            wall_clock_drift_threshold_sec=_require_int(
                item,
                "wall_clock_drift_threshold_sec",
                30,
            ),
            http_time_probe_url=_optional_str(item, "http_time_probe_url"),
            http_time_probe_timeout_sec=_require_int(item, "http_time_probe_timeout_sec", 5),
            clock_skew_threshold_sec=_require_int(item, "clock_skew_threshold_sec", 300),
            clock_anomaly_reboot_consecutive=_require_int(
                item,
                "clock_anomaly_reboot_consecutive",
                3,
            ),
            maintenance_mode_command=_optional_str(item, "maintenance_mode_command"),
            maintenance_mode_timeout_sec=_optional_int(item, "maintenance_mode_timeout_sec"),
            maintenance_grace_sec=_optional_int(item, "maintenance_grace_sec"),
            restart_threshold=_optional_int(item, "restart_threshold"),
            reboot_threshold=_optional_int(item, "reboot_threshold"),
        )

        if target.command_timeout_sec is None and target.command is not None:
            target.command_timeout_sec = global_config.default_command_timeout_sec
        if target.dependency_check_timeout_sec is None and (
            target.dns_check_command is not None or target.gateway_check_command is not None
        ):
            target.dependency_check_timeout_sec = global_config.default_command_timeout_sec
        if (
            target.maintenance_mode_timeout_sec is None
            and target.maintenance_mode_command is not None
        ):
            target.maintenance_mode_timeout_sec = global_config.default_command_timeout_sec

        _validate_target_rules(target)
        targets.append(target)

    return AppConfig(
        global_config=global_config,
        notify_config=NotifyConfig(discord=discord_config),
        targets=targets,
    )
