#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import subprocess
import time
from pathlib import Path


def _run(cmd: list[str], *, dry_run: bool) -> None:
    print("+", " ".join(cmd))
    if dry_run:
        return
    subprocess.run(cmd, check=True)


def _inject_service_down(service: str, *, dry_run: bool) -> None:
    _run(["systemctl", "stop", service], dry_run=dry_run)


def _restore_service(service: str, *, dry_run: bool) -> None:
    _run(["systemctl", "start", service], dry_run=dry_run)


def _inject_stale_file(path: Path, age_sec: int, *, dry_run: bool) -> None:
    target_ts = time.time() - max(1, age_sec)
    print(f"+ stale-file {path} age_sec={age_sec}")
    if dry_run:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text("failure-inject\n", encoding="utf-8")
    os.utime(path, (target_ts, target_ts))


def _inject_fresh_file(path: Path, *, dry_run: bool) -> None:
    print(f"+ fresh-file {path}")
    if dry_run:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text("failure-inject\n", encoding="utf-8")
    now = time.time()
    os.utime(path, (now, now))


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Inject reversible raspi-sentinel failure scenarios."
    )
    parser.add_argument("--dry-run", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    service_down = sub.add_parser("service-down", help="Stop one systemd service.")
    service_down.add_argument("--service", required=True)

    service_restore = sub.add_parser("service-restore", help="Start one systemd service.")
    service_restore.add_argument("--service", required=True)

    stale_file = sub.add_parser("stale-file", help="Set file mtime into the past.")
    stale_file.add_argument("--path", type=Path, required=True)
    stale_file.add_argument("--age-sec", type=int, default=600)

    fresh_file = sub.add_parser("fresh-file", help="Create/update file mtime to current time.")
    fresh_file.add_argument("--path", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.command == "service-down":
        _inject_service_down(args.service, dry_run=args.dry_run)
        return 0
    if args.command == "service-restore":
        _restore_service(args.service, dry_run=args.dry_run)
        return 0
    if args.command == "stale-file":
        _inject_stale_file(args.path, args.age_sec, dry_run=args.dry_run)
        return 0
    if args.command == "fresh-file":
        _inject_fresh_file(args.path, dry_run=args.dry_run)
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
