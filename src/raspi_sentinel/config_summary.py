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
            target.stats_updated_max_age_sec is not None,
            target.stats_last_input_max_age_sec is not None,
            target.stats_last_success_max_age_sec is not None,
            target.stats_records_stall_cycles is not None,
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
    if target.dns_check_command is not None:
        rules.append("dns_dependency")
    if target.gateway_check_command is not None:
        rules.append("gateway_dependency")
    if target.time_health_enabled:
        rules.append("time_health")
    return rules


def _shell_commands(target: TargetConfig) -> dict[str, str]:
    commands: dict[str, str] = {}
    if target.command is not None:
        commands["command"] = target.command
    if target.dns_check_command is not None:
        commands["dns_check_command"] = target.dns_check_command
    if target.gateway_check_command is not None:
        commands["gateway_check_command"] = target.gateway_check_command
    if target.maintenance_mode_command is not None:
        commands["maintenance_mode_command"] = target.maintenance_mode_command
    return commands


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
        ("stats_file", target.stats_file),
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


def _target_warnings(target: TargetConfig, path_entries: list[dict[str, Any]]) -> list[str]:
    warnings: list[str] = []

    if target.service_active and not target.services:
        warnings.append("service_active=true but services is empty")

    if _stats_rules_enabled(target) and target.stats_file is None:
        warnings.append("stats_* rules enabled but stats_file is unset")

    for path_entry in path_entries:
        if not path_entry["exists"]:
            warnings.append(f"{path_entry['field']} path does not exist now: {path_entry['path']}")

    for unit in target.services:
        load_state = _check_service_unit_load_state(unit)
        if load_state == "not-found":
            warnings.append(f"systemd unit not found: {unit}")

    return warnings


def _target_summary(target: TargetConfig, config: AppConfig) -> dict[str, Any]:
    shell_commands = _shell_commands(target)
    path_entries = _target_paths(target)
    warnings = _target_warnings(target, path_entries)

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
            "enabled": target.time_health_enabled,
            "check_interval_threshold_sec": target.check_interval_threshold_sec,
            "wall_clock_freeze_min_monotonic_sec": target.wall_clock_freeze_min_monotonic_sec,
            "wall_clock_freeze_max_wall_advance_sec": target.wall_clock_freeze_max_wall_advance_sec,
            "wall_clock_drift_threshold_sec": target.wall_clock_drift_threshold_sec,
            "http_time_probe_url": target.http_time_probe_url,
            "http_time_probe_timeout_sec": target.http_time_probe_timeout_sec,
            "clock_skew_threshold_sec": target.clock_skew_threshold_sec,
            "clock_anomaly_reboot_consecutive": target.clock_anomaly_reboot_consecutive,
        },
        "maintenance_mode": {
            "enabled": target.maintenance_mode_command is not None,
            "timeout_sec": target.maintenance_mode_timeout_sec,
            "grace_sec": target.maintenance_grace_sec,
        },
        "shell_commands": shell_commands,
        "warnings": warnings,
    }


def build_config_validation_report(config_path: Path, config: AppConfig) -> dict[str, Any]:
    target_summaries = [_target_summary(target, config) for target in config.targets]
    shell_command_targets = [
        summary["name"]
        for summary in target_summaries
        if isinstance(summary.get("shell_commands"), dict) and summary["shell_commands"]
    ]

    return {
        "config_path": str(config_path),
        "config_permission_warning": _config_permission_warning(config_path),
        "global": {
            "state_file": str(config.global_config.state_file),
            "events_file": str(config.global_config.events_file),
            "monitor_stats_file": str(config.global_config.monitor_stats_file),
            "loop_interval_sec": config.global_config.loop_interval_sec,
            "restart_threshold": config.global_config.restart_threshold,
            "reboot_threshold": config.global_config.reboot_threshold,
        },
        "targets": target_summaries,
        "shell_command_targets": shell_command_targets,
    }


def format_config_validation_report(report: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append("Config validation: OK")
    lines.append(f"config: {report.get('config_path')}")

    permission_warning = report.get("config_permission_warning")
    if isinstance(permission_warning, str):
        lines.append(f"warning: {permission_warning}")

    targets = report.get("targets", [])
    lines.append(f"targets: {len(targets)}")

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
