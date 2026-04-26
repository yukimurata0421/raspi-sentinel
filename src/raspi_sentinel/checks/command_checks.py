from __future__ import annotations

import logging
import shlex
import subprocess

from ..redaction import redact_command, redact_text
from .models import CheckFailure

LOG = logging.getLogger(__name__)

DEFAULT_TIMEOUT_SEC = 10


def command_check(
    command: str,
    timeout_sec: int,
    check_name: str = "command",
    use_shell: bool = False,
) -> CheckFailure | None:
    redacted_command = redact_command(command)
    # Security posture stays explicit opt-in, but shell-token detection is now advisory.
    if not use_shell and any(token in command for token in ("|", "&&", "||", ";", "$(", "`")):
        LOG.warning(
            "possible shell syntax detected with use_shell=false (check=%s): %s",
            check_name,
            redacted_command,
        )

    args: str | list[str]
    if use_shell:
        args = command
    else:
        try:
            parsed = shlex.split(command)
        except ValueError as exc:
            return CheckFailure(
                check_name,
                f"invalid command syntax: {exc}; command={redacted_command}",
            )
        if not parsed:
            return CheckFailure(check_name, "command is empty")
        args = parsed

    try:
        result = subprocess.run(
            args,
            shell=use_shell,
            check=False,
            timeout=timeout_sec,
            capture_output=True,
            text=True,
        )
    except subprocess.TimeoutExpired:
        return CheckFailure(
            check_name,
            f"command timeout after {timeout_sec}s: {redacted_command}",
        )
    except OSError as exc:
        return CheckFailure(check_name, f"command failed to start: {exc}")

    if result.returncode != 0:
        stderr = result.stderr.strip()
        stdout = result.stdout.strip()
        snippet = redact_text(stderr or stdout or "no output")
        return CheckFailure(
            check_name,
            (f"command exit code {result.returncode}: {redacted_command}; output={snippet[:300]}"),
        )

    return None


def service_active_check(
    service: str, timeout_sec: int = DEFAULT_TIMEOUT_SEC
) -> CheckFailure | None:
    try:
        result = subprocess.run(
            ["systemctl", "is-active", "--quiet", service],
            check=False,
            timeout=timeout_sec,
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


def run_command_capture(
    args: list[str],
    timeout_sec: int,
) -> tuple[subprocess.CompletedProcess[str] | None, str | None]:
    try:
        result = subprocess.run(
            args,
            check=False,
            timeout=timeout_sec,
            capture_output=True,
            text=True,
        )
    except subprocess.TimeoutExpired:
        return None, "timeout"
    except OSError:
        return None, "unavailable"
    return result, None
