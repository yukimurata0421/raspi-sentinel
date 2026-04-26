#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import shlex
import subprocess
import sys
from datetime import datetime
from pathlib import Path


class DeployError(RuntimeError):
    pass


def _run(cmd: list[str], *, dry_run: bool) -> subprocess.CompletedProcess[str]:
    print("+", " ".join(shlex.quote(x) for x in cmd))
    if dry_run:
        return subprocess.CompletedProcess(cmd, 0, "", "")
    return subprocess.run(cmd, check=False, capture_output=True, text=True)


def _run_ssh(host: str, remote_cmd: str, *, dry_run: bool) -> subprocess.CompletedProcess[str]:
    return _run(["ssh", host, remote_cmd], dry_run=dry_run)


def _require_ok(
    result: subprocess.CompletedProcess[str],
    *,
    what: str,
    command: str,
    dry_run: bool,
) -> None:
    if dry_run:
        return
    if result.returncode == 0:
        return
    detail = (result.stderr or result.stdout or "").strip()
    raise DeployError(f"{what} failed: {command}\n{detail}")


def _extract_json_object(text: str) -> dict[str, object]:
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end < 0 or end < start:
        raise DeployError(f"failed to find JSON object in output:\n{text}")
    try:
        return json.loads(text[start : end + 1])
    except json.JSONDecodeError as exc:
        raise DeployError(f"failed to decode JSON output: {exc}\n{text}") from exc


def _preflight(host: str, *, dry_run: bool) -> None:
    checks = [
        ("ssh connectivity", "hostname"),
        ("sudo non-interactive", "sudo -n true"),
        ("target path exists", "test -d /opt/raspi-sentinel"),
        ("venv python exists", "test -x /opt/raspi-sentinel/.venv/bin/python"),
        ("runtime config exists", "test -f /etc/raspi-sentinel/config.toml"),
    ]
    for label, command in checks:
        result = _run_ssh(host, command, dry_run=dry_run)
        _require_ok(result, what=label, command=command, dry_run=dry_run)


def _rsync_to_stage(local_root: Path, host: str, stage_dir: str, *, dry_run: bool) -> None:
    cmd = [
        "rsync",
        "-az",
        "--delete",
        "--exclude",
        ".git/",
        "--exclude",
        ".venv/",
        "--exclude",
        "__pycache__/",
        "--exclude",
        "*.pyc",
        "--exclude",
        ".pytest_cache/",
        "--exclude",
        ".mypy_cache/",
        "--exclude",
        ".ruff_cache/",
        f"{local_root}/",
        f"{host}:{stage_dir}/",
    ]
    result = _run(cmd, dry_run=dry_run)
    _require_ok(result, what="rsync to staging", command="rsync", dry_run=dry_run)


def _run_stage_validation(host: str, stage_dir: str, *, dry_run: bool) -> None:
    validate = (
        f"cd {shlex.quote(stage_dir)} && "
        "sudo -n /opt/raspi-sentinel/.venv/bin/python -m raspi_sentinel "
        "-c /etc/raspi-sentinel/config.toml validate-config"
    )
    result = _run_ssh(host, validate, dry_run=dry_run)
    _require_ok(result, what="staging validate-config", command=validate, dry_run=dry_run)

    dry_run_cycle = (
        f"cd {shlex.quote(stage_dir)} && "
        "sudo -n env PYTHONPATH=src /opt/raspi-sentinel/.venv/bin/python -m raspi_sentinel "
        "-c /etc/raspi-sentinel/config.toml --dry-run run-once --json"
    )
    result = _run_ssh(host, dry_run_cycle, dry_run=dry_run)
    _require_ok(result, what="staging dry-run run-once", command=dry_run_cycle, dry_run=dry_run)
    if dry_run:
        return
    payload = _extract_json_object(result.stdout)
    if payload.get("overall_status") not in ("ok", "degraded", "failed", "unknown"):
        raise DeployError(f"unexpected staging dry-run payload: {payload}")


def _switch_release(
    host: str,
    stage_dir: str,
    *,
    release_id: str,
    dry_run: bool,
) -> str:
    backup_dir = f"/opt/raspi-sentinel.rollback.{release_id}"
    remote_cmd = (
        "set -euo pipefail; "
        f"sudo -n rm -rf {shlex.quote(backup_dir)}; "
        f"sudo -n cp -a /opt/raspi-sentinel {shlex.quote(backup_dir)}; "
        f"sudo -n rsync -az --delete {shlex.quote(stage_dir)}/ /opt/raspi-sentinel/"
    )
    result = _run_ssh(host, remote_cmd, dry_run=dry_run)
    _require_ok(result, what="switch release", command=remote_cmd, dry_run=dry_run)
    return backup_dir


def _post_deploy_health_gate(host: str, *, dry_run: bool) -> None:
    checks = [
        (
            "post-deploy validate-config",
            (
                "cd /opt/raspi-sentinel && "
                "sudo -n .venv/bin/raspi-sentinel -c /etc/raspi-sentinel/config.toml validate-config"
            ),
        ),
        (
            "post-deploy dry-run cycle",
            (
                "cd /opt/raspi-sentinel && "
                "sudo -n .venv/bin/raspi-sentinel "
                "-c /etc/raspi-sentinel/config.toml --dry-run run-once --json"
            ),
        ),
        (
            "post-deploy live cycle",
            (
                "cd /opt/raspi-sentinel && "
                "sudo -n .venv/bin/raspi-sentinel "
                "-c /etc/raspi-sentinel/config.toml run-once --json"
            ),
        ),
    ]
    for label, cmd in checks:
        result = _run_ssh(host, cmd, dry_run=dry_run)
        _require_ok(result, what=label, command=cmd, dry_run=dry_run)
        if dry_run or not cmd.endswith("--json"):
            continue
        payload = _extract_json_object(result.stdout)
        if payload.get("overall_status") not in ("ok", "degraded", "failed", "unknown"):
            raise DeployError(f"unexpected post-deploy payload for {label}: {payload}")
        if payload.get("state_persisted") is not True:
            raise DeployError(f"state_persisted is not true for {label}: {payload}")


def _rollback(host: str, backup_dir: str, *, dry_run: bool) -> None:
    rollback_cmd = (
        "set -euo pipefail; "
        f"sudo -n test -d {shlex.quote(backup_dir)}; "
        f"sudo -n rsync -az --delete {shlex.quote(backup_dir)}/ /opt/raspi-sentinel/"
    )
    result = _run_ssh(host, rollback_cmd, dry_run=dry_run)
    _require_ok(result, what="rollback", command=rollback_cmd, dry_run=dry_run)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Deploy raspi-sentinel to pi5-guard with staged preflight/health gates."
    )
    parser.add_argument("--host", default="pi5-guard@pi5-guard")
    parser.add_argument(
        "--mode",
        choices=("safe", "fast"),
        default="safe",
        help="safe: staged validation + backup + post gates; fast: keep steps but skip stage validation",
    )
    parser.add_argument(
        "--stage-root",
        default="/home/pi5-guard/staging",
        help="Remote staging root directory",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print commands without executing",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    local_root = Path(__file__).resolve().parents[1]
    release_id = datetime.now().strftime("%Y%m%dT%H%M%S")
    stage_dir = f"{args.stage_root}/raspi-sentinel-{release_id}"
    backup_dir: str | None = None

    try:
        _preflight(args.host, dry_run=args.dry_run)
        mkdir_cmd = f"mkdir -p {shlex.quote(stage_dir)}"
        result = _run_ssh(args.host, mkdir_cmd, dry_run=args.dry_run)
        _require_ok(result, what="create staging directory", command=mkdir_cmd, dry_run=args.dry_run)
        _rsync_to_stage(local_root, args.host, stage_dir, dry_run=args.dry_run)
        if args.mode == "safe":
            _run_stage_validation(args.host, stage_dir, dry_run=args.dry_run)
        backup_dir = _switch_release(
            args.host,
            stage_dir,
            release_id=release_id,
            dry_run=args.dry_run,
        )
        _post_deploy_health_gate(args.host, dry_run=args.dry_run)
        print("deploy completed successfully")
        return 0
    except DeployError as exc:
        print(f"deploy failed: {exc}", file=sys.stderr)
        if backup_dir is not None:
            try:
                _rollback(args.host, backup_dir, dry_run=args.dry_run)
                print(f"rollback completed from {backup_dir}", file=sys.stderr)
            except DeployError as rollback_exc:
                print(f"rollback failed: {rollback_exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
