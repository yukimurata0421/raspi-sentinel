from __future__ import annotations

import json
import logging
import os
import platform
import shlex
import stat
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from ._version import __version__
from .config import AppConfig
from .config_summary import build_config_validation_report
from .contracts import ALLOWED_TARGET_STATUS, STATS_SCHEMA_VERSION
from .recovery import (
    network_only_failures_can_reboot,
    network_only_failures_excluded_from_reboot,
)
from .redaction import redact_text
from .state import TieredStateStore, is_storage_tiering_enabled
from .storage_verify import verify_tmpfs_storage

LOG = logging.getLogger(__name__)


def _config_permission_status(config_path: Path) -> tuple[str, str | None]:
    try:
        mode = stat.S_IMODE(config_path.stat().st_mode)
    except OSError as exc:
        return "warn", f"cannot stat config file: {exc}"
    if mode & (stat.S_IWGRP | stat.S_IWOTH):
        return "warn", f"config is group/world writable (mode={mode:04o})"
    if mode & (stat.S_IRGRP | stat.S_IROTH):
        return "warn", f"config is group/world readable (mode={mode:04o})"
    return "ok", None


def fix_config_permissions(
    *,
    config_path: Path,
    mode: int = 0o600,
    owner_uid: int = 0,
    owner_gid: int = 0,
    dry_run: bool = False,
) -> dict[str, object]:
    actions: list[str] = [
        f"chmod {mode:04o} {config_path}",
        f"chown {owner_uid}:{owner_gid} {config_path}",
    ]
    if dry_run:
        return {"status": "dry-run", "actions": actions, "detail": None}
    try:
        os.chmod(config_path, mode)
        os.chown(config_path, owner_uid, owner_gid)
    except OSError as exc:
        return {"status": "error", "actions": actions, "detail": str(exc)}
    return {"status": "ok", "actions": actions, "detail": None}


def _path_writable(path: Path) -> tuple[str, str | None]:
    try:
        path.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(prefix=".doctor-write-", dir=path)
        os.close(fd)
        Path(tmp_name).unlink(missing_ok=True)
    except OSError as exc:
        return "warn", str(exc)
    return "ok", None


def _systemd_state(unit: str, timeout_sec: int = 3) -> str:
    try:
        result = subprocess.run(
            ["systemctl", "is-active", unit],
            check=False,
            timeout=timeout_sec,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    value = (result.stdout or "").strip().lower()
    if not value:
        return "unknown"
    return value


def _load_last_run_status(stats_path: Path) -> tuple[str, int | None]:
    try:
        raw = json.loads(stats_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return "unknown", None
    stats_schema_version_raw = raw.get("stats_schema_version")
    stats_schema_version: int | None = (
        stats_schema_version_raw if isinstance(stats_schema_version_raw, int) else None
    )
    if stats_schema_version is not None and stats_schema_version > STATS_SCHEMA_VERSION:
        LOG.warning(
            ("stats file schema version is newer than supported: seen=%d supported=%d path=%s"),
            stats_schema_version,
            STATS_SCHEMA_VERSION,
            stats_path,
        )
    status = raw.get("status")
    if isinstance(status, str) and status in ALLOWED_TARGET_STATUS:
        return status, stats_schema_version
    if isinstance(status, str):
        LOG.warning("stats file has unknown status value '%s' (path=%s)", status, stats_path)
    return "unknown", stats_schema_version


def _home_path_warnings(config: AppConfig) -> list[str]:
    warnings: list[str] = []
    for target in config.targets:
        for field_name, field_path in (
            ("heartbeat_file", target.heartbeat_file),
            ("output_file", target.output_file),
            ("stats_file", target.stats.stats_file),
            ("external_status_file", target.external.external_status_file),
        ):
            if field_path is None:
                continue
            path_str = str(field_path)
            if path_str.startswith("/home/"):
                warnings.append(
                    f"target '{target.name}' uses {field_name} under /home ({path_str}); "
                    "the bundled systemd unit enables ProtectHome=true, "
                    "so timer execution may fail"
                )
    return warnings


def build_doctor_report(config_path: Path, config: AppConfig) -> dict[str, object]:
    config_perm_status, config_perm_detail = _config_permission_status(config_path)
    state_dir = config.global_config.state_file.parent
    state_dir_status, state_dir_detail = _path_writable(state_dir)
    tiering_enabled = is_storage_tiering_enabled(
        storage_require_tmpfs=config.global_config.storage_require_tmpfs,
        state_durable_file=config.global_config.state_durable_file,
        state_durable_fields=config.global_config.state_durable_fields,
    )
    tmpfs_result = verify_tmpfs_storage(config=config)

    restart_threshold = config.global_config.restart_threshold
    reboot_threshold = config.global_config.reboot_threshold
    threshold_ok = restart_threshold < reboot_threshold
    last_run_result, last_run_schema_version = _load_last_run_status(
        config.global_config.monitor_stats_file
    )
    network_only_excluded = network_only_failures_excluded_from_reboot()
    home_path_warnings = _home_path_warnings(config)

    return {
        "config_permissions": {
            "status": config_perm_status,
            "detail": config_perm_detail,
        },
        "state_dir_writable": {
            "status": state_dir_status,
            "path": str(state_dir),
            "detail": state_dir_detail,
        },
        "tmpfs": {
            "tiering_enabled": tiering_enabled,
            "verify_ok": tmpfs_result.ok,
            "reason": tmpfs_result.reason,
            "mount_path": str(tmpfs_result.mount_path),
            "mount_fs_type": tmpfs_result.mount_fs_type,
        },
        "systemd": {
            "service_state": _systemd_state("raspi-sentinel.service"),
            "timer_state": _systemd_state("raspi-sentinel.timer"),
            "tmpfs_verify_state": _systemd_state("raspi-sentinel-tmpfs-verify.service"),
        },
        "reboot_enabled": reboot_threshold > 0,
        "thresholds": {
            "restart_threshold": restart_threshold,
            "reboot_threshold": reboot_threshold,
            "status": "ok" if threshold_ok else "warn",
            "detail": None if threshold_ok else "restart_threshold must be < reboot_threshold",
        },
        "network_only_failures_excluded_from_reboot": network_only_excluded,
        # Backward compatibility field kept for v0.8.x.
        "network_only_failures_can_reboot": network_only_failures_can_reboot(),
        "path_warnings": home_path_warnings,
        "last_run_result": last_run_result,
        "last_run_stats_schema_version": last_run_schema_version,
    }


def build_explain_state_report(config: AppConfig) -> dict[str, object]:
    store = TieredStateStore(
        volatile_path=config.global_config.state_file,
        durable_path=config.global_config.state_durable_file,
        durable_fields=config.global_config.state_durable_fields,
        require_tmpfs=config.global_config.storage_require_tmpfs,
    )
    state, diagnostics = store.load_with_diagnostics()
    targets: dict[str, dict[str, object]] = {}
    for name, target_state in state.targets.items():
        targets[name] = {
            "last_status": target_state.last_status,
            "last_reason": target_state.last_reason,
            "consecutive_failures": target_state.consecutive_failures,
            "last_action": target_state.last_action,
            "last_action_ts": target_state.last_action_ts,
            "last_failure_ts": target_state.last_failure_ts,
            "last_healthy_ts": target_state.last_healthy_ts,
        }
    return {
        "state_file": str(config.global_config.state_file),
        "state_durable_file": (
            str(config.global_config.state_durable_file)
            if config.global_config.state_durable_file is not None
            else None
        ),
        "state_schema_version": state.state_schema_version,
        "limited_mode": diagnostics.limited_mode,
        "state_load_error": diagnostics.state_load_error,
        "state_corrupted": diagnostics.state_corrupted,
        "corrupt_backup_path": (
            str(diagnostics.corrupt_backup_path) if diagnostics.corrupt_backup_path else None
        ),
        "reboots_count": len(state.reboots),
        "followups_count": len(state.followups),
        "targets": targets,
    }


def _sanitize_bundle_value(value: object) -> object:
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, dict):
        return {k: _sanitize_bundle_value(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_sanitize_bundle_value(v) for v in value]
    return value


def _read_os_release() -> dict[str, str]:
    os_release = Path("/etc/os-release")
    if not os_release.exists():
        return {}
    data: dict[str, str] = {}
    for line in os_release.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or "=" not in line or line.startswith("#"):
            continue
        key, raw_value = line.split("=", 1)
        try:
            parsed = shlex.split(raw_value, posix=True)
        except ValueError:
            parsed = [raw_value.strip().strip('"').strip("'")]
        value = parsed[0] if parsed else ""
        data[key] = value
    return data


def _systemd_version() -> str | None:
    try:
        result = subprocess.run(
            ["systemctl", "--version"],
            check=False,
            timeout=3,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    first_line = (result.stdout or "").splitlines()
    if not first_line:
        return None
    return first_line[0].strip()


def _recent_events_summary(events_file: Path, *, max_lines: int = 200) -> dict[str, object]:
    if not events_file.exists():
        return {"events_file_exists": False, "events_sample_count": 0}
    try:
        lines = events_file.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        return {
            "events_file_exists": True,
            "events_read_error": str(exc),
            "events_sample_count": 0,
        }
    tail = lines[-max_lines:]
    kind_counts: dict[str, int] = {}
    reason_counts: dict[str, int] = {}
    for line in tail:
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        kind = record.get("kind")
        if isinstance(kind, str):
            kind_counts[kind] = kind_counts.get(kind, 0) + 1
        reason = record.get("reason")
        if isinstance(reason, str):
            reason_counts[reason] = reason_counts.get(reason, 0) + 1
    return {
        "events_file_exists": True,
        "events_sample_count": len(tail),
        "kind_counts": kind_counts,
        "reason_counts": reason_counts,
    }


def build_support_bundle(
    config_path: Path,
    config: AppConfig,
    *,
    doctor_report: dict[str, object] | None = None,
) -> dict[str, object]:
    report = doctor_report or build_doctor_report(config_path=config_path, config=config)
    explain_state = build_explain_state_report(config=config)
    validation_report = build_config_validation_report(config_path=config_path, config=config)
    last_run_result, last_run_schema = _load_last_run_status(
        config.global_config.monitor_stats_file
    )
    bundle: dict[str, object] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "raspi_sentinel_version": __version__,
        "python_version": platform.python_version(),
        "platform": {
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
            "os_release": _read_os_release(),
            "systemd_version": _systemd_version(),
            "argv0": sys.argv[0] if sys.argv else None,
        },
        "config_path": str(config_path),
        "doctor": report,
        "config_validation": validation_report,
        "state": explain_state,
        "storage_tier": {
            "require_tmpfs": config.global_config.storage_require_tmpfs,
            "state_durable_file": (
                str(config.global_config.state_durable_file)
                if config.global_config.state_durable_file is not None
                else None
            ),
            "state_durable_fields": list(config.global_config.state_durable_fields),
        },
        "events_summary": _recent_events_summary(config.global_config.events_file),
        "last_result_from_monitor_stats": {
            "status": last_run_result,
            "stats_schema_version": last_run_schema,
            "note": "This reflects monitor stats snapshot, not a newly executed dry-run cycle.",
        },
    }
    return _sanitize_bundle_value(bundle)  # type: ignore[return-value]
