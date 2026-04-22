from __future__ import annotations

import argparse
import json
import logging
import time
from pathlib import Path

from .config import AppConfig, load_config
from .config_summary import build_config_validation_report, format_config_validation_report
from .engine import CycleReport, run_cycle_collect
from .exit_codes import (
    CONFIG_LOAD_FAILED,
    INVALID_INTERVAL,
    REBOOT_REQUESTED,
    STORAGE_VERIFY_FAILED,
    VALIDATION_WARNING,
)
from .logging_utils import configure_logging
from .storage_verify import verify_tmpfs_storage
from .state_helpers import safe_int

LOG = logging.getLogger(__name__)


def _run_cycle_collect(config: AppConfig, dry_run: bool) -> tuple[int, CycleReport]:
    rc, report = run_cycle_collect(
        config=config,
        dry_run=dry_run,
        time_provider=time.time,
        mono_provider=time.monotonic,
    )
    return rc, report


def _run_cycle(config: AppConfig, dry_run: bool) -> int:
    rc, _ = _run_cycle_collect(config=config, dry_run=dry_run)
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
        rc, report = _run_cycle_collect(config=config, dry_run=args.dry_run)
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
            rc = _run_cycle(config=config, dry_run=args.dry_run)
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
        )
        if args.json:
            print(json.dumps(verify_result.to_dict(), indent=2, sort_keys=True))
        else:
            LOG.info("storage verify result: %s", verify_result.to_dict())
        return 0 if verify_result.ok else STORAGE_VERIFY_FAILED

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
