#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
from pathlib import Path

CORE_UNITS = (
    "raspi-sentinel.service",
    "raspi-sentinel.timer",
    "raspi-sentinel-tmpfs-verify.service",
)
OPTIONAL_UNITS = ("run-raspi\\x2dsentinel.mount",)


def _run(cmd: list[str], *, dry_run: bool) -> None:
    print("+", " ".join(cmd))
    if dry_run:
        return
    subprocess.run(cmd, check=True)


def _install_file(src: Path, dst: Path, *, mode: int, dry_run: bool) -> None:
    print(f"+ install {src} -> {dst} mode={mode:04o}")
    if dry_run:
        return
    shutil.copyfile(src, dst)
    os.chmod(dst, mode)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Install raspi-sentinel systemd units.")
    parser.add_argument("--source-dir", type=Path, default=Path("systemd"))
    parser.add_argument("--dest-dir", type=Path, default=Path("/etc/systemd/system"))
    parser.add_argument("--include-tmpfs-mount", action="store_true")
    parser.add_argument("--enable-timer", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    unit_names = list(CORE_UNITS)
    if args.include_tmpfs_mount:
        unit_names.extend(OPTIONAL_UNITS)

    for unit_name in unit_names:
        src = args.source_dir / unit_name
        dst = args.dest_dir / unit_name
        if not src.exists():
            raise FileNotFoundError(f"missing unit file: {src}")
        _install_file(src, dst, mode=0o644, dry_run=args.dry_run)

    _run(["systemctl", "daemon-reload"], dry_run=args.dry_run)
    if args.enable_timer:
        _run(["systemctl", "enable", "--now", "raspi-sentinel.timer"], dry_run=args.dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
