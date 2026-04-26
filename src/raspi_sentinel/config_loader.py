from __future__ import annotations

import logging
import stat
import tomllib
from pathlib import Path
from typing import Any, Mapping

from .config_models import (
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

LOG = logging.getLogger(__name__)

_DEFAULT_DURABLE_FIELDS: tuple[str, ...] = (
    "reboot_history",
    "followup_schedule",
    "notify_backlog",
)
_DURABLE_FIELD_ALIASES: dict[str, str] = {
    "reboot_history": "reboot_history",
    "reboots": "reboot_history",
    "followup_schedule": "followup_schedule",
    "followups": "followup_schedule",
    "notify_backlog": "notify_backlog",
    "notify_delivery_backlog": "notify_backlog",
}


def _require_int(data: Mapping[str, object], key: str, default: int | None = None) -> int:
    value = data.get(key, default)
    if not isinstance(value, int):
        raise ValueError(f"'{key}' must be an integer")
    return value


def _optional_int(data: Mapping[str, object], key: str) -> int | None:
    value = data.get(key)
    if value is None:
        return None
    if not isinstance(value, int):
        raise ValueError(f"'{key}' must be an integer")
    return value


def _optional_str(data: Mapping[str, object], key: str) -> str | None:
    value = data.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"'{key}' must be a non-empty string when set")
    return value


def _optional_path(data: Mapping[str, object], key: str) -> Path | None:
    value = _optional_str(data, key)
    return Path(value) if value else None


def _optional_bool(data: Mapping[str, object], key: str, default: bool) -> bool:
    value = data.get(key, default)
    if not isinstance(value, bool):
        raise ValueError(f"'{key}' must be boolean")
    return value


def _optional_str_list(data: Mapping[str, object], key: str) -> list[str] | None:
    value = data.get(key)
    if value is None:
        return None
    if not isinstance(value, list) or any(not isinstance(v, str) or not v.strip() for v in value):
        raise ValueError(f"'{key}' must be an array of non-empty strings")
    return [v.strip() for v in value]


def _optional_float_from_mapping(data: Mapping[str, Any], key: str) -> float | None:
    value = data.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"'{key}' must be a number")
    return float(value)


def _parse_state_durable_fields(storage_raw: Mapping[str, object]) -> tuple[str, ...]:
    raw = storage_raw.get("state_durable_fields")
    if raw is None:
        return _DEFAULT_DURABLE_FIELDS
    if not isinstance(raw, list):
        raise ValueError("[storage].state_durable_fields must be an array of strings")
    fields: list[str] = []
    for item in raw:
        if not isinstance(item, str) or not item.strip():
            raise ValueError("[storage].state_durable_fields must be an array of non-empty strings")
        canonical = _DURABLE_FIELD_ALIASES.get(item.strip().lower())
        if canonical is None:
            raise ValueError(
                (
                    "[storage].state_durable_fields contains unknown value "
                    f"'{item}'; supported: reboot_history, followup_schedule, notify_backlog"
                )
            )
        if canonical not in fields:
            fields.append(canonical)
    return tuple(fields)


def _validate_target_rules(target: TargetConfig) -> None:
    deps = target.deps
    network = target.network
    stats = target.stats
    time_health = target.time_health
    maintenance = target.maintenance
    external = target.external

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

    if target.command_use_shell and target.command is None:
        raise ValueError(
            f"target '{target.name}': command_use_shell=true requires command to be set"
        )

    if deps.dns_check_use_shell and deps.dns_check_command is None:
        raise ValueError(
            f"target '{target.name}': dns_check_use_shell=true requires dns_check_command"
        )

    if deps.gateway_check_use_shell and deps.gateway_check_command is None:
        raise ValueError(
            (f"target '{target.name}': gateway_check_use_shell=true requires gateway_check_command")
        )

    if deps.link_check_use_shell and deps.link_check_command is None:
        raise ValueError(
            f"target '{target.name}': link_check_use_shell=true requires link_check_command"
        )

    if deps.default_route_check_use_shell and deps.default_route_check_command is None:
        raise ValueError(
            (
                f"target '{target.name}': default_route_check_use_shell=true requires "
                "default_route_check_command"
            )
        )

    if deps.internet_ip_check_use_shell and deps.internet_ip_check_command is None:
        raise ValueError(
            (
                f"target '{target.name}': internet_ip_check_use_shell=true requires "
                "internet_ip_check_command"
            )
        )

    if deps.dns_server_check_use_shell and deps.dns_server_check_command is None:
        raise ValueError(
            (
                f"target '{target.name}': dns_server_check_use_shell=true requires "
                "dns_server_check_command"
            )
        )

    if deps.wan_vs_target_check_use_shell and deps.wan_vs_target_check_command is None:
        raise ValueError(
            (
                f"target '{target.name}': wan_vs_target_check_use_shell=true requires "
                "wan_vs_target_check_command"
            )
        )

    if network.network_probe_enabled:
        if network.network_interface is None:
            raise ValueError(
                f"target '{target.name}': network_probe_enabled=true requires network_interface"
            )
        if network.gateway_probe_timeout_sec <= 0:
            raise ValueError(f"target '{target.name}': gateway_probe_timeout_sec must be > 0")
        if not network.internet_ip_targets:
            raise ValueError(
                f"target '{target.name}': internet_ip_targets must have at least one target"
            )

    degraded_threshold = network.consecutive_failure_thresholds.get("degraded", 2)
    failed_threshold = network.consecutive_failure_thresholds.get("failed", 6)
    if degraded_threshold <= 0 or failed_threshold <= 0:
        raise ValueError(
            f"target '{target.name}': consecutive_failure_thresholds values must be > 0"
        )
    if failed_threshold < degraded_threshold:
        raise ValueError(
            f"target '{target.name}': consecutive_failure_thresholds.failed must be >= degraded"
        )

    if deps.dependency_check_timeout_sec is not None and deps.dependency_check_timeout_sec <= 0:
        raise ValueError(f"target '{target.name}': dependency_check_timeout_sec must be > 0")

    if (
        maintenance.maintenance_mode_timeout_sec is not None
        and maintenance.maintenance_mode_timeout_sec <= 0
    ):
        raise ValueError(f"target '{target.name}': maintenance_mode_timeout_sec must be > 0")

    if maintenance.maintenance_mode_use_shell and maintenance.maintenance_mode_command is None:
        raise ValueError(
            (
                f"target '{target.name}': maintenance_mode_use_shell=true requires "
                "maintenance_mode_command"
            )
        )

    if maintenance.maintenance_grace_sec is not None and maintenance.maintenance_grace_sec < 0:
        raise ValueError(f"target '{target.name}': maintenance_grace_sec must be >= 0")

    if target.restart_threshold is not None and target.restart_threshold <= 0:
        raise ValueError(f"target '{target.name}': restart_threshold must be > 0")

    if target.reboot_threshold is not None and target.reboot_threshold <= 0:
        raise ValueError(f"target '{target.name}': reboot_threshold must be > 0")

    if target.reboot_threshold is not None and target.restart_threshold is not None:
        if target.reboot_threshold <= target.restart_threshold:
            raise ValueError(
                f"target '{target.name}': reboot_threshold must be > restart_threshold"
            )

    if stats.stats_updated_max_age_sec is not None and stats.stats_updated_max_age_sec <= 0:
        raise ValueError(f"target '{target.name}': stats_updated_max_age_sec must be > 0")

    if stats.stats_last_input_max_age_sec is not None and stats.stats_last_input_max_age_sec <= 0:
        raise ValueError(f"target '{target.name}': stats_last_input_max_age_sec must be > 0")

    if (
        stats.stats_last_success_max_age_sec is not None
        and stats.stats_last_success_max_age_sec <= 0
    ):
        raise ValueError(f"target '{target.name}': stats_last_success_max_age_sec must be > 0")

    if stats.stats_records_stall_cycles is not None and stats.stats_records_stall_cycles <= 0:
        raise ValueError(f"target '{target.name}': stats_records_stall_cycles must be > 0")

    if (
        external.external_status_updated_max_age_sec is not None
        and external.external_status_updated_max_age_sec <= 0
    ):
        raise ValueError(f"target '{target.name}': external_status_updated_max_age_sec must be > 0")
    if (
        external.external_status_last_progress_max_age_sec is not None
        and external.external_status_last_progress_max_age_sec <= 0
    ):
        raise ValueError(
            f"target '{target.name}': external_status_last_progress_max_age_sec must be > 0"
        )
    if (
        external.external_status_last_success_max_age_sec is not None
        and external.external_status_last_success_max_age_sec <= 0
    ):
        raise ValueError(
            f"target '{target.name}': external_status_last_success_max_age_sec must be > 0"
        )
    if external.external_status_startup_grace_sec < 0:
        raise ValueError(f"target '{target.name}': external_status_startup_grace_sec must be >= 0")

    has_external_status_rule = any(
        [
            external.external_status_updated_max_age_sec is not None,
            external.external_status_last_progress_max_age_sec is not None,
            external.external_status_last_success_max_age_sec is not None,
        ]
    )
    if has_external_status_rule and external.external_status_file is None:
        raise ValueError(
            (
                f"target '{target.name}': external_status_file is required when "
                "external_status_* checks are configured"
            )
        )

    if target.service_active and not target.services:
        raise ValueError(
            f"target '{target.name}': when service_active=true, "
            "services must list at least one unit"
        )

    if time_health.wall_clock_freeze_min_monotonic_sec <= 0:
        raise ValueError(f"target '{target.name}': wall_clock_freeze_min_monotonic_sec must be > 0")

    if time_health.check_interval_threshold_sec <= 0:
        raise ValueError(f"target '{target.name}': check_interval_threshold_sec must be > 0")

    if time_health.wall_clock_freeze_max_wall_advance_sec < 0:
        raise ValueError(
            f"target '{target.name}': wall_clock_freeze_max_wall_advance_sec must be >= 0"
        )

    if time_health.wall_clock_drift_threshold_sec <= 0:
        raise ValueError(f"target '{target.name}': wall_clock_drift_threshold_sec must be > 0")

    if time_health.http_time_probe_timeout_sec <= 0:
        raise ValueError(f"target '{target.name}': http_time_probe_timeout_sec must be > 0")

    if time_health.clock_skew_threshold_sec <= 0:
        raise ValueError(f"target '{target.name}': clock_skew_threshold_sec must be > 0")

    if time_health.clock_anomaly_reboot_consecutive <= 0:
        raise ValueError(f"target '{target.name}': clock_anomaly_reboot_consecutive must be > 0")

    has_stats_rule = any(
        [
            stats.stats_updated_max_age_sec is not None,
            stats.stats_last_input_max_age_sec is not None,
            stats.stats_last_success_max_age_sec is not None,
            stats.stats_records_stall_cycles is not None,
        ]
    )
    if has_stats_rule and stats.stats_file is None:
        raise ValueError(
            f"target '{target.name}': stats_file is required when stats_* checks are configured"
        )

    has_rule = any(
        [
            target.service_active,
            target.heartbeat_file is not None,
            target.output_file is not None,
            target.command is not None,
            stats.stats_file is not None,
            external.external_status_file is not None,
            deps.dns_check_command is not None,
            deps.dns_server_check_command is not None,
            deps.gateway_check_command is not None,
            deps.link_check_command is not None,
            deps.default_route_check_command is not None,
            deps.internet_ip_check_command is not None,
            deps.wan_vs_target_check_command is not None,
            network.network_probe_enabled,
            time_health.time_health_enabled,
        ]
    )
    if not has_rule:
        raise ValueError(
            f"target '{target.name}': at least one rule is required "
            "(service_active, heartbeat, output, command, stats_file, external_status_file, "
            "dns_check_command, dns_server_check_command, gateway_check_command, "
            "link_check_command, default_route_check_command, internet_ip_check_command, "
            "wan_vs_target_check_command, network_probe_enabled, time_health_enabled)"
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
    storage_raw = raw.get("storage", {})
    if storage_raw is None:
        storage_raw = {}
    if not isinstance(storage_raw, dict):
        raise ValueError("[storage] must be a table")

    state_file = Path(global_raw.get("state_file", "/var/lib/raspi-sentinel/state.json"))
    state_durable_file: Path | None = None
    events_file = Path(global_raw.get("events_file", "/var/lib/raspi-sentinel/events.jsonl"))
    monitor_stats_file = Path(
        global_raw.get("monitor_stats_file", "/var/lib/raspi-sentinel/stats.json")
    )
    state_durable_fields: tuple[str, ...] = ()
    storage_require_tmpfs = False
    storage_verify_min_free_bytes = 1_048_576
    storage_verify_write_bytes = 4096
    storage_verify_cooldown_sec = 2

    if storage_raw:
        state_file = Path(storage_raw.get("state_volatile_path", str(state_file)))
        state_durable_file = Path(
            storage_raw.get("state_durable_path", "/var/lib/raspi-sentinel/state.durable.json")
        )
        events_file = Path(storage_raw.get("events_path", str(events_file)))
        monitor_stats_file = Path(storage_raw.get("snapshot_path", str(monitor_stats_file)))
        state_durable_fields = _parse_state_durable_fields(storage_raw)
        storage_require_tmpfs = _optional_bool(storage_raw, "require_tmpfs", False)
        storage_verify_min_free_bytes = _require_int(
            storage_raw, "verify_min_free_bytes", storage_verify_min_free_bytes
        )
        storage_verify_write_bytes = _require_int(
            storage_raw, "verify_write_bytes", storage_verify_write_bytes
        )
        storage_verify_cooldown_sec = _require_int(
            storage_raw, "verify_cooldown_sec", storage_verify_cooldown_sec
        )

    global_config = GlobalConfig(
        state_file=state_file,
        state_durable_file=state_durable_file,
        state_durable_fields=state_durable_fields,
        state_max_file_bytes=_require_int(global_raw, "state_max_file_bytes", 2_000_000),
        state_reboots_max_entries=_require_int(global_raw, "state_reboots_max_entries", 256),
        state_lock_timeout_sec=_require_int(global_raw, "state_lock_timeout_sec", 5),
        events_file=events_file,
        events_max_file_bytes=_require_int(global_raw, "events_max_file_bytes", 5_000_000),
        events_backup_generations=_require_int(global_raw, "events_backup_generations", 3),
        monitor_stats_file=monitor_stats_file,
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
        storage_require_tmpfs=storage_require_tmpfs,
        storage_verify_min_free_bytes=storage_verify_min_free_bytes,
        storage_verify_write_bytes=storage_verify_write_bytes,
        storage_verify_cooldown_sec=storage_verify_cooldown_sec,
    )

    if global_config.restart_threshold <= 0:
        raise ValueError("global restart_threshold must be > 0")
    if global_config.reboot_threshold <= global_config.restart_threshold:
        raise ValueError("global reboot_threshold must be > restart_threshold")
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
    if global_config.storage_verify_min_free_bytes <= 0:
        raise ValueError("storage verify_min_free_bytes must be > 0")
    if global_config.storage_verify_write_bytes <= 0:
        raise ValueError("storage verify_write_bytes must be > 0")
    if global_config.storage_verify_cooldown_sec < 0:
        raise ValueError("storage verify_cooldown_sec must be >= 0")

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
    notify_on_recovery = discord_raw.get("notify_on_recovery", True)
    if not isinstance(notify_on_recovery, bool):
        raise ValueError("[notify.discord].notify_on_recovery must be boolean")

    discord_config = DiscordNotifyConfig(
        enabled=discord_enabled,
        webhook_url=discord_webhook,
        username=discord_username,
        timeout_sec=_require_int(discord_raw, "timeout_sec", 5),
        followup_delay_sec=_require_int(discord_raw, "followup_delay_sec", 300),
        retry_interval_sec=_require_int(discord_raw, "retry_interval_sec", 60),
        heartbeat_interval_sec=_require_int(discord_raw, "heartbeat_interval_sec", 300),
        notify_on_recovery=notify_on_recovery,
    )

    if discord_config.timeout_sec <= 0:
        raise ValueError("[notify.discord].timeout_sec must be > 0")
    if discord_config.followup_delay_sec <= 0:
        raise ValueError("[notify.discord].followup_delay_sec must be > 0")
    if discord_config.retry_interval_sec <= 0:
        raise ValueError("[notify.discord].retry_interval_sec must be > 0")
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

        name_raw = item.get("name")
        if not isinstance(name_raw, str) or not name_raw.strip():
            raise ValueError("target name must be a non-empty string")
        name = name_raw.strip()
        if name in seen_names:
            raise ValueError(f"duplicate target name: {name}")
        seen_names.add(name)

        services_raw = item.get("services", [])
        if not isinstance(services_raw, list) or any(not isinstance(s, str) for s in services_raw):
            raise ValueError(f"target '{name}': services must be an array of strings")
        services = [s.strip() for s in services_raw]
        if any(not s for s in services):
            raise ValueError(f"target '{name}': services must not contain empty names")

        service_active = item.get("service_active", True)
        if not isinstance(service_active, bool):
            raise ValueError(f"target '{name}': service_active must be boolean")

        thresholds_raw = item.get("consecutive_failure_thresholds", {})
        if not isinstance(thresholds_raw, dict):
            raise ValueError(f"target '{name}': consecutive_failure_thresholds must be a table")
        latency_raw = item.get("latency_thresholds_ms", {})
        if not isinstance(latency_raw, dict):
            raise ValueError(f"target '{name}': latency_thresholds_ms must be a table")
        loss_raw = item.get("packet_loss_thresholds_pct", {})
        if not isinstance(loss_raw, dict):
            raise ValueError(f"target '{name}': packet_loss_thresholds_pct must be a table")

        deps = DependencyCheckConfig(
            dns_check_command=_optional_str(item, "dns_check_command"),
            dns_check_use_shell=_optional_bool(item, "dns_check_use_shell", False),
            dns_server_check_command=_optional_str(item, "dns_server_check_command"),
            dns_server_check_use_shell=_optional_bool(item, "dns_server_check_use_shell", False),
            gateway_check_command=_optional_str(item, "gateway_check_command"),
            gateway_check_use_shell=_optional_bool(item, "gateway_check_use_shell", False),
            link_check_command=_optional_str(item, "link_check_command"),
            link_check_use_shell=_optional_bool(item, "link_check_use_shell", False),
            default_route_check_command=_optional_str(item, "default_route_check_command"),
            default_route_check_use_shell=_optional_bool(
                item, "default_route_check_use_shell", False
            ),
            internet_ip_check_command=_optional_str(item, "internet_ip_check_command"),
            internet_ip_check_use_shell=_optional_bool(item, "internet_ip_check_use_shell", False),
            wan_vs_target_check_command=_optional_str(item, "wan_vs_target_check_command"),
            wan_vs_target_check_use_shell=_optional_bool(
                item, "wan_vs_target_check_use_shell", False
            ),
            dependency_check_timeout_sec=_optional_int(item, "dependency_check_timeout_sec"),
        )
        network = NetworkProbeConfig(
            network_probe_enabled=_optional_bool(item, "network_probe_enabled", False),
            network_interface=_optional_str(item, "network_interface"),
            gateway_probe_timeout_sec=_require_int(item, "gateway_probe_timeout_sec", 2),
            internet_ip_targets=_optional_str_list(item, "internet_ip_targets")
            or ["1.1.1.1", "8.8.8.8"],
            dns_query_target=_optional_str(item, "dns_query_target"),
            http_probe_target=_optional_str(item, "http_probe_target"),
            consecutive_failure_thresholds={
                "degraded": _require_int(thresholds_raw, "degraded", 2),
                "failed": _require_int(thresholds_raw, "failed", 6),
            },
            latency_thresholds_ms={
                key: value
                for key, value in (
                    ("gateway", _optional_float_from_mapping(latency_raw, "gateway")),
                    ("internet_ip", _optional_float_from_mapping(latency_raw, "internet_ip")),
                    ("dns", _optional_float_from_mapping(latency_raw, "dns")),
                    ("http_total", _optional_float_from_mapping(latency_raw, "http_total")),
                )
                if value is not None
            },
            packet_loss_thresholds_pct={
                key: value
                for key, value in (
                    ("gateway", _optional_float_from_mapping(loss_raw, "gateway")),
                    ("internet_ip", _optional_float_from_mapping(loss_raw, "internet_ip")),
                )
                if value is not None
            },
        )
        stats_cfg = StatsCheckConfig(
            stats_file=_optional_path(item, "stats_file"),
            stats_updated_max_age_sec=_optional_int(item, "stats_updated_max_age_sec"),
            stats_last_input_max_age_sec=_optional_int(item, "stats_last_input_max_age_sec"),
            stats_last_success_max_age_sec=_optional_int(item, "stats_last_success_max_age_sec"),
            stats_records_stall_cycles=_optional_int(item, "stats_records_stall_cycles"),
        )
        th_cfg = TimeHealthCheckConfig(
            time_health_enabled=_optional_bool(item, "time_health_enabled", False),
            check_interval_threshold_sec=_require_int(item, "check_interval_threshold_sec", 30),
            wall_clock_freeze_min_monotonic_sec=_require_int(
                item, "wall_clock_freeze_min_monotonic_sec", 25
            ),
            wall_clock_freeze_max_wall_advance_sec=_require_int(
                item, "wall_clock_freeze_max_wall_advance_sec", 1
            ),
            wall_clock_drift_threshold_sec=_require_int(item, "wall_clock_drift_threshold_sec", 30),
            http_time_probe_url=_optional_str(item, "http_time_probe_url"),
            http_time_probe_timeout_sec=_require_int(item, "http_time_probe_timeout_sec", 5),
            clock_skew_threshold_sec=_require_int(item, "clock_skew_threshold_sec", 300),
            clock_anomaly_reboot_consecutive=_require_int(
                item, "clock_anomaly_reboot_consecutive", 3
            ),
        )
        maint_cfg = MaintenanceCheckConfig(
            maintenance_mode_command=_optional_str(item, "maintenance_mode_command"),
            maintenance_mode_use_shell=_optional_bool(item, "maintenance_mode_use_shell", False),
            maintenance_mode_timeout_sec=_optional_int(item, "maintenance_mode_timeout_sec"),
            maintenance_grace_sec=_optional_int(item, "maintenance_grace_sec"),
        )
        ext_cfg = ExternalStatusCheckConfig(
            external_status_file=_optional_path(item, "external_status_file"),
            external_status_updated_max_age_sec=_optional_int(
                item, "external_status_updated_max_age_sec"
            ),
            external_status_last_progress_max_age_sec=_optional_int(
                item, "external_status_last_progress_max_age_sec"
            ),
            external_status_last_success_max_age_sec=_optional_int(
                item, "external_status_last_success_max_age_sec"
            ),
            external_status_startup_grace_sec=_require_int(
                item, "external_status_startup_grace_sec", 120
            ),
            external_status_unhealthy_values=tuple(
                [
                    value.strip().lower()
                    for value in (
                        _optional_str_list(item, "external_status_unhealthy_values") or []
                    )
                    if value.strip()
                ]
            )
            or ("failed", "unhealthy"),
        )

        target = TargetConfig(
            name=name,
            services=services,
            service_active=service_active,
            heartbeat_file=_optional_path(item, "heartbeat_file"),
            heartbeat_max_age_sec=_optional_int(item, "heartbeat_max_age_sec"),
            output_file=_optional_path(item, "output_file"),
            output_max_age_sec=_optional_int(item, "output_max_age_sec"),
            command=_optional_str(item, "command"),
            command_use_shell=_optional_bool(item, "command_use_shell", False),
            command_timeout_sec=_optional_int(item, "command_timeout_sec"),
            restart_threshold=_optional_int(item, "restart_threshold"),
            reboot_threshold=_optional_int(item, "reboot_threshold"),
            deps=deps,
            network=network,
            stats=stats_cfg,
            time_health=th_cfg,
            maintenance=maint_cfg,
            external=ext_cfg,
        )

        if target.command_timeout_sec is None:
            target.command_timeout_sec = global_config.default_command_timeout_sec
        if deps.dependency_check_timeout_sec is None:
            deps.dependency_check_timeout_sec = global_config.default_command_timeout_sec
        if (
            maint_cfg.maintenance_mode_timeout_sec is None
            and maint_cfg.maintenance_mode_command is not None
        ):
            maint_cfg.maintenance_mode_timeout_sec = global_config.default_command_timeout_sec

        _validate_target_rules(target)
        targets.append(target)

    return AppConfig(
        global_config=global_config,
        notify_config=NotifyConfig(discord=discord_config),
        targets=targets,
    )
