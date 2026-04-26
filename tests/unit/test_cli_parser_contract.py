from __future__ import annotations

from pathlib import Path

import pytest

from raspi_sentinel import cli


def test_parser_accepts_send_notifications_global_option() -> None:
    parser = cli._build_parser()
    args = parser.parse_args(["--dry-run", "--send-notifications", "run-once"])
    assert args.dry_run is True
    assert args.send_notifications is True
    assert args.command == "run-once"


def test_parser_accepts_send_notifications_for_loop_subcommand() -> None:
    parser = cli._build_parser()
    args = parser.parse_args(["--dry-run", "--send-notifications", "loop", "--interval-sec", "30"])
    assert args.command == "loop"
    assert args.send_notifications is True
    assert args.interval_sec == 30


def test_parser_accepts_doctor_support_bundle_and_permission_fix_options() -> None:
    parser = cli._build_parser()
    args = parser.parse_args(
        [
            "doctor",
            "--json",
            "--fix-permissions",
            "--fix-permissions-dry-run",
            "--support-bundle",
            "/tmp/support-bundle.json",
        ]
    )
    assert args.command == "doctor"
    assert args.fix_permissions is True
    assert args.fix_permissions_dry_run is True
    assert args.support_bundle == Path("/tmp/support-bundle.json")


def test_parser_accepts_export_prometheus_subcommand() -> None:
    parser = cli._build_parser()
    args = parser.parse_args(
        [
            "export-prometheus",
            "--textfile-path",
            "/tmp/raspi-sentinel.prom",
        ]
    )
    assert args.command == "export-prometheus"
    assert args.textfile_path == Path("/tmp/raspi-sentinel.prom")


def test_cli_supports_global_version_flag(capsys: pytest.CaptureFixture[str]) -> None:
    rc = cli.main(["--version"])
    assert rc == 0
    assert "raspi-sentinel 0.9.0" in capsys.readouterr().out
