from __future__ import annotations

import stat
import subprocess
from pathlib import Path
from typing import Any

from .config import AppConfig, TargetConfig


def _config_permission_warning(config_path: Path) -> str | None:
    try:
        mode = stat.S_IMODE(config_path.stat().st_mode)
    except OSError as exc:
        return f"cannot stat config file: {exc}"
    if mode & (stat.S_IWGRP | stat.S_IWOTH):
        return (
            f"config file is group/world-writable (mode={mode:04o}); "
            "use chmod go-w on trusted admin-owned config"
        )
    return None


def _stats_rules_enabled(target: TargetConfig) -> bool:
    return any(
        (
            target.stats.stats_updated_max_age_sec is not None,
            target.stats.stats_last_input_max_age_sec is not None,
            target.stats.stats_last_success_max_age_sec is not None,
            target.stats.stats_records_stall_cycles is not None,
        )
    )


def _external_status_rules_enabled(target: TargetConfig) -> bool:
    return any(
        (
            target.external.external_status_updated_max_age_sec is not None,
            target.external.external_status_last_progress_max_age_sec is not None,
            target.external.external_status_last_success_max_age_sec is not None,
        )
    )


def _enabled_rules(target: TargetConfig) -> list[str]:
    rules: list[str] = []
    if target.service_active:
        rules.append("service_active")
    if target.heartbeat_file is not None and target.heartbeat_max_age_sec is not None:
        rules.append("heartbeat_freshness")
    if target.output_file is not None and target.output_max_age_sec is not None:
        rules.append("output_freshness")
    if target.command is not None:
        rules.append("command")
    if _stats_rules_enabled(target):
        rules.append("semantic_stats")
    if _external_status_rules_enabled(target):
        rules.append("external_status")
    if target.deps.dns_check_command is not None:
        rules.append("dns_dependency")
    if target.deps.dns_server_check_command is not None:
        rules.append("dns_server_dependency")
    if target.deps.gateway_check_command is not None:
        rules.append("gateway_dependency")
    if target.deps.link_check_command is not None:
        rules.append("link_dependency")
    if target.deps.default_route_check_command is not None:
        rules.append("default_route_dependency")
    if target.deps.internet_ip_check_command is not None:
        rules.append("internet_ip_dependency")
    if target.deps.wan_vs_target_check_command is not None:
        rules.append("wan_target_dependency")
    if target.network.network_probe_enabled:
        rules.append("network_probe")
    if target.time_health.time_health_enabled:
        rules.append("time_health")
    return rules


def _shell_commands(target: TargetConfig) -> dict[str, str]:
    commands: dict[str, str] = {}
    if target.command is not None:
        commands["command"] = target.command
    if target.deps.dns_check_command is not None:
        commands["dns_check_command"] = target.deps.dns_check_command
    if target.deps.dns_server_check_command is not None:
        commands["dns_server_check_command"] = target.deps.dns_server_check_command
    if target.deps.gateway_check_command is not None:
        commands["gateway_check_command"] = target.deps.gateway_check_command
    if target.deps.link_check_command is not None:
        commands["link_check_command"] = target.deps.link_check_command
    if target.deps.default_route_check_command is not None:
        commands["default_route_check_command"] = target.deps.default_route_check_command
    if target.deps.internet_ip_check_command is not None:
        commands["internet_ip_check_command"] = target.deps.internet_ip_check_command
    if target.deps.wan_vs_target_check_command is not None:
        commands["wan_vs_target_check_command"] = target.deps.wan_vs_target_check_command
    if target.maintenance.maintenance_mode_command is not None:
        commands["maintenance_mode_command"] = target.maintenance.maintenance_mode_command
    return commands


def _shell_opt_in_checks(target: TargetConfig) -> list[str]:
    checks: list[str] = []
    if target.command is not None and target.command_use_shell:
        checks.append("command")
    if target.deps.dns_check_command is not None and target.deps.dns_check_use_shell:
        checks.append("dns_check_command")
    if target.deps.dns_server_check_command is not None and target.deps.dns_server_check_use_shell:
        checks.append("dns_server_check_command")
    if target.deps.gateway_check_command is not None and target.deps.gateway_check_use_shell:
        checks.append("gateway_check_command")
    if target.deps.link_check_command is not None and target.deps.link_check_use_shell:
        checks.append("link_check_command")
    if (
        target.deps.default_route_check_command is not None
        and target.deps.default_route_check_use_shell
    ):
        checks.append("default_route_check_command")
    if (
        target.deps.internet_ip_check_command is not None
        and target.deps.internet_ip_check_use_shell
    ):
        checks.append("internet_ip_check_command")
    if (
        target.deps.wan_vs_target_check_command is not None
        and target.deps.wan_vs_target_check_use_shell
    ):
        checks.append("wan_vs_target_check_command")
    if (
        target.maintenance.maintenance_mode_command is not None
        and target.maintenance.maintenance_mode_use_shell
    ):
        checks.append("maintenance_mode_command")
    return checks


def _check_service_unit_load_state(unit: str, timeout_sec: int = 3) -> str | None:
    try:
        result = subprocess.run(
            ["systemctl", "show", "-p", "LoadState", "--value", unit],
            check=False,
            timeout=timeout_sec,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        return None
    except (OSError, subprocess.SubprocessError):
        return "unknown"

    if result.returncode != 0:
        return "unknown"
    value = result.stdout.strip().lower()
    return value or "unknown"


def _target_paths(target: TargetConfig) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for field_name, path in (
        ("heartbeat_file", target.heartbeat_file),
        ("output_file", target.output_file),
        ("stats_file", target.stats.stats_file),
        ("external_status_file", target.external.external_status_file),
    ):
        if path is None:
            continue
        entries.append(
            {
                "field": field_name,
                "path": str(path),
                "exists": path.exists(),
            }
        )
    return entries


def _target_warnings(
    target: TargetConfig,
    path_entries: list[dict[str, Any]],
    config_permission_warning: str | None,
) -> list[str]:
    warnings: list[str] = []
    shell_tokens = ("|", "&&", "||", ";", "$(", "`")

    if target.service_active and not target.services:
        warnings.append("service_active=true but services is empty")

    if _stats_rules_enabled(target) and target.stats.stats_file is None:
        warnings.append("stats_* rules enabled but stats_file is unset")
    if _external_status_rules_enabled(target) and target.external.external_status_file is None:
        warnings.append("external_status_* rules enabled but external_status_file is unset")

    for path_entry in path_entries:
        if not path_entry["exists"]:
            warnings.append(f"{path_entry['field']} path does not exist now: {path_entry['path']}")

    for unit in target.services:
        load_state = _check_service_unit_load_state(unit)
        if load_state == "not-found":
            warnings.append(f"systemd unit not found: {unit}")

    if (
        config_permission_warning is not None
        and _shell_opt_in_checks(target)
        and "group/world-writable" in config_permission_warning
    ):
        warnings.append(
            "config is writable and shell execution is opt-in for this target; tighten permissions"
        )

    restart_threshold = target.restart_threshold
    reboot_threshold = target.reboot_threshold
    if (
        restart_threshold is not None
        and reboot_threshold is not None
        and reboot_threshold < restart_threshold
    ):
        warnings.append("reboot_threshold is lower than restart_threshold")

    if target.time_health.time_health_enabled and (
        target.time_health.check_interval_threshold_sec
        > target.time_health.wall_clock_freeze_min_monotonic_sec
    ):
        warnings.append(
            (
                "time-health thresholds look inconsistent: "
                "check_interval_threshold_sec > wall_clock_freeze_min_monotonic_sec"
            )
        )

    for command_field, command in _shell_commands(target).items():
        if any(token in command for token in shell_tokens):
            if command_field in _shell_opt_in_checks(target):
                continue
            warnings.append(
                (
                    f"{command_field} contains shell tokens but *_use_shell is false; "
                    "command may behave unexpectedly"
                )
            )

    return warnings


def _global_warnings(config: AppConfig) -> list[str]:
    warnings: list[str] = []
    gc = config.global_config
    if gc.restart_threshold >= gc.reboot_threshold:
        warnings.append("global restart_threshold should be lower than reboot_threshold")
    if gc.storage_require_tmpfs and not str(gc.state_file).startswith("/run/"):
        warnings.append(
            (
                "storage.require_tmpfs=true but state_volatile path is not under /run; "
                f"verify volatile path intent ({gc.state_file})"
            )
        )
    return warnings


def _target_summary(
    target: TargetConfig,
    config: AppConfig,
    config_permission_warning: str | None,
) -> dict[str, Any]:
    shell_commands = _shell_commands(target)
    shell_opt_in_checks = _shell_opt_in_checks(target)
    path_entries = _target_paths(target)
    warnings = _target_warnings(target, path_entries, config_permission_warning)

    services: list[dict[str, Any]] = []
    for unit in target.services:
        services.append(
            {
                "name": unit,
                "load_state": _check_service_unit_load_state(unit),
            }
        )

    return {
        "name": target.name,
        "enabled_rules": _enabled_rules(target),
        "services": services,
        "paths": path_entries,
        "effective_thresholds": {
            "restart_threshold": target.restart_threshold or config.global_config.restart_threshold,
            "reboot_threshold": target.reboot_threshold or config.global_config.reboot_threshold,
        },
        "time_health": {
            "enabled": target.time_health.time_health_enabled,
            "check_interval_threshold_sec": target.time_health.check_interval_threshold_sec,
            "wall_clock_freeze_min_monotonic_sec": (
                target.time_health.wall_clock_freeze_min_monotonic_sec
            ),
            "wall_clock_freeze_max_wall_advance_sec": (
                target.time_health.wall_clock_freeze_max_wall_advance_sec
            ),
            "wall_clock_drift_threshold_sec": target.time_health.wall_clock_drift_threshold_sec,
            "http_time_probe_url": target.time_health.http_time_probe_url,
            "http_time_probe_timeout_sec": target.time_health.http_time_probe_timeout_sec,
            "clock_skew_threshold_sec": target.time_health.clock_skew_threshold_sec,
            "clock_anomaly_reboot_consecutive": target.time_health.clock_anomaly_reboot_consecutive,
        },
        "network_probe": {
            "enabled": target.network.network_probe_enabled,
            "network_interface": target.network.network_interface,
            "gateway_probe_timeout_sec": target.network.gateway_probe_timeout_sec,
            "internet_ip_targets": target.network.internet_ip_targets,
            "dns_query_target": target.network.dns_query_target,
            "http_probe_target": target.network.http_probe_target,
            "consecutive_failure_thresholds": target.network.consecutive_failure_thresholds,
            "latency_thresholds_ms": target.network.latency_thresholds_ms,
            "packet_loss_thresholds_pct": target.network.packet_loss_thresholds_pct,
        },
        "maintenance_mode": {
            "enabled": target.maintenance.maintenance_mode_command is not None,
            "timeout_sec": target.maintenance.maintenance_mode_timeout_sec,
            "grace_sec": target.maintenance.maintenance_grace_sec,
        },
        "external_status": {
            "file": (
                str(target.external.external_status_file)
                if target.external.external_status_file
                else None
            ),
            "updated_max_age_sec": target.external.external_status_updated_max_age_sec,
            "last_progress_max_age_sec": target.external.external_status_last_progress_max_age_sec,
            "last_success_max_age_sec": target.external.external_status_last_success_max_age_sec,
            "startup_grace_sec": target.external.external_status_startup_grace_sec,
            "unhealthy_values": list(target.external.external_status_unhealthy_values),
        },
        "shell_commands": shell_commands,
        "shell_opt_in_checks": shell_opt_in_checks,
        "warnings": warnings,
    }


def build_config_validation_report(config_path: Path, config: AppConfig) -> dict[str, Any]:
    permission_warning = _config_permission_warning(config_path)
    global_warnings = _global_warnings(config)
    target_summaries = [
        _target_summary(target, config, permission_warning) for target in config.targets
    ]
    shell_command_targets = [
        summary["name"]
        for summary in target_summaries
        if isinstance(summary.get("shell_commands"), dict) and summary["shell_commands"]
    ]

    return {
        "config_path": str(config_path),
        "config_permission_warning": permission_warning,
        "global_warnings": global_warnings,
        "global": {
            "state_file": str(config.global_config.state_file),
            "state_durable_file": (
                str(config.global_config.state_durable_file)
                if config.global_config.state_durable_file is not None
                else None
            ),
            "state_durable_fields": list(config.global_config.state_durable_fields),
            "state_max_file_bytes": config.global_config.state_max_file_bytes,
            "state_reboots_max_entries": config.global_config.state_reboots_max_entries,
            "state_lock_timeout_sec": config.global_config.state_lock_timeout_sec,
            "events_file": str(config.global_config.events_file),
            "events_max_file_bytes": config.global_config.events_max_file_bytes,
            "events_backup_generations": config.global_config.events_backup_generations,
            "monitor_stats_file": str(config.global_config.monitor_stats_file),
            "storage_require_tmpfs": config.global_config.storage_require_tmpfs,
            "storage_verify_min_free_bytes": config.global_config.storage_verify_min_free_bytes,
            "storage_verify_write_bytes": config.global_config.storage_verify_write_bytes,
            "storage_verify_cooldown_sec": config.global_config.storage_verify_cooldown_sec,
            "loop_interval_sec": config.global_config.loop_interval_sec,
            "restart_threshold": config.global_config.restart_threshold,
            "reboot_threshold": config.global_config.reboot_threshold,
        },
        "targets": target_summaries,
        "shell_command_targets": shell_command_targets,
        "warning_count": _count_warnings_from_target_summaries(
            target_summaries,
            permission_warning,
            global_warnings,
        ),
    }


def _count_warnings_from_target_summaries(
    target_summaries: list[dict[str, Any]],
    config_permission_warning: str | None,
    global_warnings: list[str],
) -> int:
    count = (1 if config_permission_warning else 0) + len(global_warnings)
    for summary in target_summaries:
        warnings = summary.get("warnings", [])
        if isinstance(warnings, list):
            count += len(warnings)
    return count


def format_config_validation_report(report: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append("Config validation: OK")
    lines.append(f"config: {report.get('config_path')}")

    permission_warning = report.get("config_permission_warning")
    if isinstance(permission_warning, str):
        lines.append(f"warning: {permission_warning}")
    global_warnings = report.get("global_warnings", [])
    if isinstance(global_warnings, list):
        for warning in global_warnings:
            lines.append(f"warning: {warning}")

    targets = report.get("targets", [])
    lines.append(f"targets: {len(targets)}")
    lines.append(f"warnings: {report.get('warning_count', 0)}")

    shell_targets = report.get("shell_command_targets", [])
    if shell_targets:
        lines.append("targets using shell commands: " + ", ".join(shell_targets))
    else:
        lines.append("targets using shell commands: none")

    for target in targets:
        name = target.get("name", "unknown")
        lines.append("")
        lines.append(f"[{name}]")

        enabled_rules = target.get("enabled_rules", [])
        if enabled_rules:
            lines.append("rules: " + ", ".join(enabled_rules))
        else:
            lines.append("rules: none")

        thresholds = target.get("effective_thresholds", {})
        lines.append(
            "thresholds: "
            f"restart={thresholds.get('restart_threshold')} "
            f"reboot={thresholds.get('reboot_threshold')}"
        )

        time_health = target.get("time_health", {})
        lines.append(f"time_health: {'enabled' if time_health.get('enabled') else 'disabled'}")

        maintenance_mode = target.get("maintenance_mode", {})
        lines.append(
            f"maintenance_mode: {'enabled' if maintenance_mode.get('enabled') else 'disabled'}"
        )

        shell_commands = target.get("shell_commands", {})
        if shell_commands:
            lines.append("shell_commands:")
            for key in sorted(shell_commands):
                lines.append(f"  - {key}: {shell_commands[key]}")

        shell_opt_in_checks = target.get("shell_opt_in_checks", [])
        if shell_opt_in_checks:
            lines.append("shell_opt_in_checks: " + ", ".join(shell_opt_in_checks))

        services = target.get("services", [])
        if services:
            lines.append("services:")
            for service in services:
                lines.append(
                    f"  - {service.get('name')} (load_state={service.get('load_state', 'n/a')})"
                )

        paths = target.get("paths", [])
        if paths:
            lines.append("paths:")
            for path_entry in paths:
                exists_label = "exists" if path_entry.get("exists") else "missing"
                lines.append(
                    f"  - {path_entry.get('field')}: {path_entry.get('path')} ({exists_label})"
                )

        warnings = target.get("warnings", [])
        if warnings:
            lines.append("warnings:")
            for warning in warnings:
                lines.append(f"  - {warning}")

    return "\n".join(lines)
