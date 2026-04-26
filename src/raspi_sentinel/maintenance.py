from __future__ import annotations

import logging
import shlex
import subprocess

from .config import TargetConfig
from .state_models import TargetState

LOG = logging.getLogger(__name__)


def run_command_success(command: str, timeout_sec: int, use_shell: bool) -> bool:
    if not use_shell and any(token in command for token in ("|", "&&", "||", ";", "$(", "`")):
        LOG.warning(
            "possible shell syntax detected with use_shell=false (maintenance): %s",
            command,
        )
    args: str | list[str]
    if use_shell:
        args = command
    else:
        try:
            args = shlex.split(command)
        except ValueError:
            return False
        if not args:
            LOG.warning("maintenance command is empty after parsing; treating as no-match")
            return False
    try:
        result = subprocess.run(
            args,
            shell=use_shell,
            check=False,
            timeout=timeout_sec,
            capture_output=True,
            text=True,
        )
    except (subprocess.TimeoutExpired, OSError):
        return False
    return result.returncode == 0


def is_target_suppressed_by_maintenance(
    target: TargetConfig,
    target_state: TargetState,
    now_ts: float,
) -> tuple[bool, str]:
    suppress_until = target_state.maintenance_suppress_until_ts or 0.0

    if now_ts < suppress_until:
        remain = int(suppress_until - now_ts)
        return True, f"grace active ({remain}s remaining)"

    command = target.maintenance.maintenance_mode_command
    if not command:
        return False, ""

    timeout = target.maintenance.maintenance_mode_timeout_sec or 10
    matched = run_command_success(
        command=command,
        timeout_sec=timeout,
        use_shell=bool(target.maintenance.maintenance_mode_use_shell),
    )
    if not matched:
        return False, ""

    grace_sec = target.maintenance.maintenance_grace_sec or 0
    if grace_sec > 0:
        target_state.maintenance_suppress_until_ts = now_ts + grace_sec
    return True, "maintenance mode command matched"
