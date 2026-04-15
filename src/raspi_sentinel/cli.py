from __future__ import annotations

import argparse
import json
import logging
import time
from pathlib import Path

from .config import AppConfig, load_config
from .config_summary import build_config_validation_report, format_config_validation_report
from .engine import run_cycle_collect
from .logging_utils import configure_logging
from .state_helpers import safe_int

LOG = logging.getLogger(__name__)


def _run_cycle_collect(config: AppConfig, dry_run: bool) -> tuple[int, dict[str, object]]:
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

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    configure_logging(verbose=args.verbose)

    try:
        config = load_config(args.config)
    except Exception as exc:
        LOG.error("failed to load config '%s': %s", args.config, exc)
        return 10

    if args.command == "run-once":
        rc, report = _run_cycle_collect(config=config, dry_run=args.dry_run)
        if args.json:
            print(json.dumps(report, indent=2, sort_keys=True))
        return rc

    if args.command == "loop":
        interval = args.interval_sec or config.global_config.loop_interval_sec
        if interval <= 0:
            LOG.error("loop interval must be > 0")
            return 11

        LOG.info("starting loop mode interval=%ss", interval)
        while True:
            rc = _run_cycle(config=config, dry_run=args.dry_run)
            if rc == 2:
                LOG.error("reboot was requested; exiting loop")
                return 0
            time.sleep(interval)

    # args.command == "validate-config" (only remaining subcommand)
    report = build_config_validation_report(config_path=args.config, config=config)
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(format_config_validation_report(report))
    warning_count = safe_int(report.get("warning_count"), 0)
    if args.strict and warning_count > 0:
        LOG.error("validate-config strict mode failed: warnings=%s", report["warning_count"])
        return 15
    return 0
