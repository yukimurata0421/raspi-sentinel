#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
from pathlib import Path

CORE_UNITS = (
    "raspi-sentinel.service",
    "raspi-sentinel.timer",
    "raspi-sentinel-tmpfs-verify.service",
)
OPTIONAL_UNITS = ("run-raspi\\x2dsentinel.mount",)
_SERVICE_UNITS = {"raspi-sentinel.service", "raspi-sentinel-tmpfs-verify.service"}


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


def _resolve_raspi_sentinel_bin(explicit_path: str | None) -> str:
    if explicit_path:
        if not os.path.isabs(explicit_path):
            raise ValueError("--raspi-sentinel-bin must be an absolute path")
        return explicit_path
    detected = shutil.which("raspi-sentinel")
    if detected:
        if not os.path.isabs(detected):
            raise ValueError("resolved raspi-sentinel executable path must be absolute")
        return detected
    raise FileNotFoundError(
        "could not resolve raspi-sentinel executable from PATH; "
        "install package first or pass --raspi-sentinel-bin"
    )


def _validate_execstart_visibility(path: str) -> None:
    if path.startswith("/home/"):
        raise ValueError(
            "raspi-sentinel binary is under /home, but bundled service uses ProtectHome=true. "
            "install into /opt/raspi-sentinel/.venv (or another system-visible path) "
            "and pass that path via --raspi-sentinel-bin."
        )


def render_service_unit(
    source_text: str,
    *,
    raspi_sentinel_bin: str,
    config_path: Path,
) -> str:
    pattern = r"^(\s*)ExecStart=\S*raspi-sentinel\s+"
    rendered = re.sub(
        pattern,
        lambda m: f"{m.group(1)}ExecStart={raspi_sentinel_bin} ",
        source_text,
        flags=re.MULTILINE,
    )
    rendered = rendered.replace(
        "-c /etc/raspi-sentinel/config.toml ",
        f"-c {config_path} ",
    )
    return rendered


def _render_service_unit(
    *,
    src: Path,
    dst: Path,
    raspi_sentinel_bin: str,
    config_path: Path,
    dry_run: bool,
) -> None:
    print(f"+ render {src} -> {dst} bin={raspi_sentinel_bin} config={config_path}")
    text = render_service_unit(
        src.read_text(encoding="utf-8"),
        raspi_sentinel_bin=raspi_sentinel_bin,
        config_path=config_path,
    )
    if dry_run:
        return
    dst.write_text(text, encoding="utf-8")
    os.chmod(dst, 0o644)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Install raspi-sentinel systemd units.",
        epilog=(
            "If storage tmpfs tiering is enabled in config, pass --include-tmpfs-mount "
            "to install run-raspi\\x2dsentinel.mount as well."
        ),
    )
    parser.add_argument("--source-dir", type=Path, default=Path("systemd"))
    parser.add_argument("--dest-dir", type=Path, default=Path("/etc/systemd/system"))
    parser.add_argument(
        "--raspi-sentinel-bin",
        type=str,
        default=None,
        help=(
            "Absolute path to raspi-sentinel binary for systemd ExecStart "
            "(auto-detected by default)."
        ),
    )
    parser.add_argument(
        "--config-path",
        type=Path,
        default=Path("/etc/raspi-sentinel/config.toml"),
        help="Config path embedded into rendered systemd service units.",
    )
    parser.add_argument(
        "--include-tmpfs-mount",
        action="store_true",
        help="Install run-raspi\\x2dsentinel.mount for /run/raspi-sentinel tmpfs.",
    )
    parser.add_argument("--enable-timer", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    unit_names = list(CORE_UNITS)
    raspi_sentinel_bin = _resolve_raspi_sentinel_bin(args.raspi_sentinel_bin)
    _validate_execstart_visibility(raspi_sentinel_bin)
    if args.include_tmpfs_mount:
        unit_names.extend(OPTIONAL_UNITS)

    for unit_name in unit_names:
        src = args.source_dir / unit_name
        dst = args.dest_dir / unit_name
        if not src.exists():
            raise FileNotFoundError(f"missing unit file: {src}")
        if unit_name in _SERVICE_UNITS:
            _render_service_unit(
                src=src,
                dst=dst,
                raspi_sentinel_bin=raspi_sentinel_bin,
                config_path=args.config_path,
                dry_run=args.dry_run,
            )
        else:
            _install_file(src, dst, mode=0o644, dry_run=args.dry_run)

    _run(["systemctl", "daemon-reload"], dry_run=args.dry_run)
    if args.include_tmpfs_mount:
        _run(["systemctl", "enable", "--now", "run-raspi\\x2dsentinel.mount"], dry_run=args.dry_run)
    if args.enable_timer:
        _run(["systemctl", "enable", "--now", "raspi-sentinel.timer"], dry_run=args.dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
