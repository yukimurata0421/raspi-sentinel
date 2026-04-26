from __future__ import annotations

from pathlib import Path

from raspi_sentinel import cli


def test_parser_accepts_send_notifications_global_option() -> None:
    parser = cli._build_parser()
    args = parser.parse_args(["--dry-run", "--send-notifications", "run-once"])
    assert args.dry_run is True
    assert args.send_notifications is True
    assert args.command == "run-once"


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
