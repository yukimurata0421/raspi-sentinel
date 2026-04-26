from __future__ import annotations

import argparse
import json
import logging
import time
from pathlib import Path

from .config import AppConfig, load_config
from .config_summary import build_config_validation_report, format_config_validation_report
from .diagnostics import (
    build_doctor_report,
    build_explain_state_report,
    build_support_bundle,
    fix_config_permissions,
)
from .engine import CycleReport, run_cycle_collect
from .exit_codes import (
    CONFIG_LOAD_FAILED,
    INVALID_INTERVAL,
    REBOOT_REQUESTED,
    STORAGE_VERIFY_FAILED,
    VALIDATION_WARNING,
)
from .logging_utils import configure_logging
from .state_helpers import safe_int
from .storage_verify import verify_tmpfs_storage

LOG = logging.getLogger(__name__)


def _run_cycle_collect(
    config: AppConfig,
    dry_run: bool,
    send_notifications_in_dry_run: bool = False,
) -> tuple[int, CycleReport]:
    rc, report = run_cycle_collect(
        config=config,
        dry_run=dry_run,
        time_provider=time.time,
        mono_provider=time.monotonic,
        send_notifications_in_dry_run=send_notifications_in_dry_run,
    )
    return rc, report


def _run_cycle(
    config: AppConfig,
    dry_run: bool,
    send_notifications_in_dry_run: bool = False,
) -> int:
    rc, _ = _run_cycle_collect(
        config=config,
        dry_run=dry_run,
        send_notifications_in_dry_run=send_notifications_in_dry_run,
    )
    return rc


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="raspi-sentinel: staged logical self-healing recovery for Raspberry Pi"
    )
    parser.add_argument(
        "-c",
        "--config",
        type=Path,
        default=Path("/etc/raspi-sentinel/config.toml"),
        help="Path to TOML config file",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Evaluate and log actions but do not restart/reboot",
    )
    parser.add_argument(
        "--send-notifications",
        action="store_true",
        help="Allow notification delivery during --dry-run (default: disabled in dry-run).",
    )
    parser.add_argument("--verbose", action="store_true", help="Enable debug logging")
    parser.add_argument(
        "--structured-logging",
        action="store_true",
        help="Emit logs as JSON lines for machine ingestion",
    )

    sub = parser.add_subparsers(dest="command", required=True)
    run_once_parser = sub.add_parser("run-once", help="Run one health/recovery evaluation cycle")
    run_once_parser.add_argument(
        "--json",
        action="store_true",
        help="Print one-cycle evaluation result as JSON",
    )

    loop_parser = sub.add_parser("loop", help="Run health checks in a continuous loop")
    loop_parser.add_argument(
        "--interval-sec",
        type=int,
        default=None,
        help="Override loop interval (default comes from [global].loop_interval_sec)",
    )

    validate_parser = sub.add_parser(
        "validate-config",
        help="Validate config and print rule summary",
    )
    validate_parser.add_argument(
        "--json",
        action="store_true",
        help="Print config validation summary as JSON",
    )
    validate_parser.add_argument(
        "--strict",
        action="store_true",
        help="Return non-zero when config summary contains warnings",
    )

    verify_storage_parser = sub.add_parser(
        "verify-storage",
        help="Verify tmpfs-backed volatile storage before starting monitor cycle",
    )
    verify_storage_parser.add_argument(
        "--json",
        action="store_true",
        help="Print storage verification result as JSON",
    )
    verify_storage_parser.add_argument(
        "--expected-mode",
        type=lambda value: int(value, 8),
        default=0o755,
        help="Expected mount directory mode in octal (default: 0755)",
    )
    verify_storage_parser.add_argument(
        "--expected-owner-uid",
        type=int,
        default=0,
        help="Expected mount directory owner uid (default: 0)",
    )
    verify_storage_parser.add_argument(
        "--expected-owner-gid",
        type=int,
        default=0,
        help="Expected mount directory owner gid (default: 0)",
    )
    verify_storage_parser.add_argument(
        "--no-cooldown",
        action="store_true",
        help="Skip post-verify cooldown sleep for ad-hoc CLI checks",
    )
    doctor_parser = sub.add_parser(
        "doctor",
        help="Run operator-facing environment checks (config, storage, systemd, thresholds)",
    )
    doctor_parser.add_argument(
        "--json",
        action="store_true",
        help="Print doctor checks as JSON (default output format; retained for compatibility)",
    )
    doctor_parser.add_argument(
        "--fix-permissions",
        action="store_true",
        help="Apply root-owned 0600 permissions to config file before running doctor checks",
    )
    doctor_parser.add_argument(
        "--fix-permissions-dry-run",
        action="store_true",
        help="Show config permission fix actions without applying them",
    )
    doctor_parser.add_argument(
        "--support-bundle",
        type=Path,
        default=None,
        help="Write sanitized support bundle JSON to the specified path",
    )
    explain_state_parser = sub.add_parser(
        "explain-state",
        help="Print a concise view of persisted runtime state and diagnostics",
    )
    explain_state_parser.add_argument(
        "--json",
        action="store_true",
        help="Print state explanation as JSON (default output format; retained for compatibility)",
    )
    prometheus_parser = sub.add_parser(
        "export-prometheus",
        help="Write one-shot Prometheus textfile metrics from doctor/explain-state snapshots",
    )
    prometheus_parser.add_argument(
        "--textfile-path",
        type=Path,
        required=True,
        help="Path to write Prometheus textfile metrics",
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    configure_logging(verbose=args.verbose, structured=args.structured_logging)

    try:
        config = load_config(args.config)
    except Exception as exc:
        LOG.error("failed to load config '%s': %s", args.config, exc)
        return CONFIG_LOAD_FAILED

    if args.command == "run-once":
        rc, report = _run_cycle_collect(
            config=config,
            dry_run=args.dry_run,
            send_notifications_in_dry_run=args.send_notifications,
        )
        if args.json:
            print(json.dumps(report, indent=2, sort_keys=True))
        return rc

    if args.command == "loop":
        interval = args.interval_sec or config.global_config.loop_interval_sec
        if interval <= 0:
            LOG.error("loop interval must be > 0")
            return INVALID_INTERVAL

        LOG.info("starting loop mode interval=%ss", interval)
        while True:
            rc = _run_cycle(
                config=config,
                dry_run=args.dry_run,
                send_notifications_in_dry_run=args.send_notifications,
            )
            if rc == REBOOT_REQUESTED:
                LOG.error("reboot was requested; exiting loop")
                return 0
            time.sleep(interval)

    if args.command == "verify-storage":
        verify_result = verify_tmpfs_storage(
            config=config,
            expected_mode=args.expected_mode,
            expected_owner_uid=args.expected_owner_uid,
            expected_owner_gid=args.expected_owner_gid,
            apply_cooldown=not args.no_cooldown,
        )
        if args.json:
            print(json.dumps(verify_result.to_dict(), indent=2, sort_keys=True))
        else:
            LOG.info("storage verify result: %s", verify_result.to_dict())
        return 0 if verify_result.ok else STORAGE_VERIFY_FAILED
    if args.command == "doctor":
        fix_result: dict[str, object] | None = None
        if args.fix_permissions:
            fix_result = fix_config_permissions(
                config_path=args.config,
                dry_run=args.fix_permissions_dry_run,
            )
        doctor_report = build_doctor_report(config_path=args.config, config=config)
        if fix_result is not None:
            doctor_report["fix_permissions"] = fix_result
        if args.support_bundle is not None:
            bundle = build_support_bundle(config_path=args.config, config=config)
            args.support_bundle.parent.mkdir(parents=True, exist_ok=True)
            args.support_bundle.write_text(
                json.dumps(bundle, indent=2, sort_keys=True), encoding="utf-8"
            )
            doctor_report["support_bundle_path"] = str(args.support_bundle)
        print(json.dumps(doctor_report, indent=2, sort_keys=True))
        return 0
    if args.command == "explain-state":
        state_report = build_explain_state_report(config=config)
        print(json.dumps(state_report, indent=2, sort_keys=True))
        return 0
    if args.command == "export-prometheus":
        doctor_report = build_doctor_report(config_path=args.config, config=config)
        state_report = build_explain_state_report(config=config)
        lines = _prometheus_lines(doctor_report=doctor_report, state_report=state_report)
        args.textfile_path.parent.mkdir(parents=True, exist_ok=True)
        args.textfile_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return 0

    # args.command == "validate-config" (only remaining subcommand)
    config_report = build_config_validation_report(config_path=args.config, config=config)
    if args.json:
        print(json.dumps(config_report, indent=2, sort_keys=True))
    else:
        print(format_config_validation_report(config_report))
    warning_count = safe_int(config_report.get("warning_count"), 0)
    if args.strict and warning_count > 0:
        LOG.error(
            "validate-config strict mode failed: warnings=%s",
            config_report["warning_count"],
        )
        return VALIDATION_WARNING
    return 0


def _prometheus_bool(value: object) -> int:
    return 1 if value is True else 0


def _gauge(name: str, help_text: str, value: int) -> list[str]:
    return [
        f"# HELP {name} {help_text}",
        f"# TYPE {name} gauge",
        f"{name} {value}",
    ]


def _prometheus_lines(
    *,
    doctor_report: dict[str, object],
    state_report: dict[str, object],
) -> list[str]:
    config_status = doctor_report.get("config_permissions", {})
    thresholds = doctor_report.get("thresholds", {})
    tmpfs = doctor_report.get("tmpfs", {})
    systemd = doctor_report.get("systemd", {})
    config_ok = _prometheus_bool(
        isinstance(config_status, dict) and config_status.get("status") == "ok"
    )
    thresholds_ok = _prometheus_bool(
        isinstance(thresholds, dict) and thresholds.get("status") == "ok"
    )
    tmpfs_ok = _prometheus_bool(isinstance(tmpfs, dict) and tmpfs.get("verify_ok") is True)
    timer_active = _prometheus_bool(
        isinstance(systemd, dict) and systemd.get("timer_state") == "active"
    )
    limited_mode = _prometheus_bool(state_report.get("limited_mode") is True)
    reboots_count = safe_int(state_report.get("reboots_count"), 0)
    followups_count = safe_int(state_report.get("followups_count"), 0)
    lines = [
        *_gauge(
            "raspi_sentinel_doctor_config_permissions_ok",
            "1 when config permissions are ok.",
            config_ok,
        ),
        *_gauge(
            "raspi_sentinel_doctor_thresholds_ok",
            "1 when restart/reboot thresholds are valid.",
            thresholds_ok,
        ),
        *_gauge(
            "raspi_sentinel_doctor_tmpfs_verify_ok",
            "1 when tmpfs verification passes.",
            tmpfs_ok,
        ),
        *_gauge(
            "raspi_sentinel_doctor_timer_active",
            "1 when raspi-sentinel.timer is active.",
            timer_active,
        ),
        *_gauge(
            "raspi_sentinel_state_limited_mode",
            "1 when state loading is in limited mode.",
            limited_mode,
        ),
        *_gauge(
            "raspi_sentinel_state_reboots_count",
            "Number of reboot records in state.",
            reboots_count,
        ),
        *_gauge(
            "raspi_sentinel_state_followups_count",
            "Number of pending followups in state.",
            followups_count,
        ),
    ]
    return lines
