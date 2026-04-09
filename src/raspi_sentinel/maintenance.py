from __future__ import annotations

import subprocess
from typing import Any


def run_shell_success(command: str, timeout_sec: int) -> bool:
    try:
        result = subprocess.run(
            command,
            shell=True,
            check=False,
            timeout=timeout_sec,
            capture_output=True,
            text=True,
        )
    except (subprocess.TimeoutExpired, OSError):
        return False
    return result.returncode == 0


def is_target_suppressed_by_maintenance(
    target: Any,
    target_state: dict[str, Any],
    now_ts: float,
) -> tuple[bool, str]:
    suppress_until_raw = target_state.get("maintenance_suppress_until_ts", 0)
    try:
        suppress_until = float(suppress_until_raw)
    except (TypeError, ValueError):
        suppress_until = 0.0

    if now_ts < suppress_until:
        remain = int(suppress_until - now_ts)
        return True, f"grace active ({remain}s remaining)"

    command = target.maintenance_mode_command
    if not command:
        return False, ""

    timeout = target.maintenance_mode_timeout_sec or 10
    matched = run_shell_success(command=command, timeout_sec=timeout)
    if not matched:
        return False, ""

    grace_sec = target.maintenance_grace_sec or 0
    if grace_sec > 0:
        target_state["maintenance_suppress_until_ts"] = now_ts + grace_sec
    return True, "maintenance mode command matched"
