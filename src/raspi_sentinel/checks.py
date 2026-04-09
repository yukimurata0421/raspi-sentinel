from __future__ import annotations

from dataclasses import dataclass
import logging
from pathlib import Path
import subprocess
import time

from .config import TargetConfig

LOG = logging.getLogger(__name__)


@dataclass(slots=True)
class CheckFailure:
    check: str
    message: str


@dataclass(slots=True)
class CheckResult:
    target: str
    healthy: bool
    failures: list[CheckFailure]


def _file_freshness_check(path: Path, max_age_sec: int, check_name: str) -> CheckFailure | None:
    try:
        stat = path.stat()
    except FileNotFoundError:
        return CheckFailure(check_name, f"file missing: {path}")
    except OSError as exc:
        return CheckFailure(check_name, f"cannot stat file {path}: {exc}")

    age = time.time() - stat.st_mtime
    if age > max_age_sec:
        return CheckFailure(
            check_name,
            f"file stale: {path} age={age:.1f}s max={max_age_sec}s",
        )
    return None


def _command_check(command: str, timeout_sec: int) -> CheckFailure | None:
    try:
        result = subprocess.run(
            command,
            shell=True,
            check=False,
            timeout=timeout_sec,
            capture_output=True,
            text=True,
        )
    except subprocess.TimeoutExpired:
        return CheckFailure("command", f"command timeout after {timeout_sec}s: {command}")
    except OSError as exc:
        return CheckFailure("command", f"command failed to start: {exc}")

    if result.returncode != 0:
        stderr = result.stderr.strip()
        stdout = result.stdout.strip()
        snippet = stderr or stdout or "no output"
        return CheckFailure(
            "command",
            f"command exit code {result.returncode}: {command}; output={snippet[:300]}",
        )

    return None


def _service_active_check(service: str) -> CheckFailure | None:
    try:
        result = subprocess.run(
            ["systemctl", "is-active", "--quiet", service],
            check=False,
            timeout=10,
            capture_output=True,
            text=True,
        )
    except subprocess.TimeoutExpired:
        return CheckFailure("service_active", f"systemctl is-active timeout for service {service}")
    except OSError as exc:
        return CheckFailure("service_active", f"cannot run systemctl for {service}: {exc}")

    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "inactive"
        return CheckFailure("service_active", f"service not active: {service} ({detail})")

    return None


def run_checks(target: TargetConfig) -> CheckResult:
    failures: list[CheckFailure] = []

    if target.heartbeat_file is not None and target.heartbeat_max_age_sec is not None:
        failure = _file_freshness_check(
            target.heartbeat_file,
            target.heartbeat_max_age_sec,
            "heartbeat_file",
        )
        if failure:
            failures.append(failure)

    if target.output_file is not None and target.output_max_age_sec is not None:
        failure = _file_freshness_check(target.output_file, target.output_max_age_sec, "output_file")
        if failure:
            failures.append(failure)

    if target.command:
        timeout_sec = target.command_timeout_sec or 10
        failure = _command_check(target.command, timeout_sec)
        if failure:
            failures.append(failure)

    if target.service_active:
        for service in target.services:
            failure = _service_active_check(service)
            if failure:
                failures.append(failure)

    healthy = not failures
    if healthy:
        LOG.debug("target '%s' passed all health checks", target.name)
    else:
        LOG.warning(
            "target '%s' failed checks: %s",
            target.name,
            "; ".join(f"{f.check}: {f.message}" for f in failures),
        )

    return CheckResult(target=target.name, healthy=healthy, failures=failures)
