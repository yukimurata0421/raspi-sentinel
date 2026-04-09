from __future__ import annotations

from typing import Any


def safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def safe_optional_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def target_state(state: dict[str, Any], target_name: str) -> dict[str, Any]:
    targets = state.setdefault("targets", {})
    current = targets.get(target_name)
    if not isinstance(current, dict):
        current = {}
        targets[target_name] = current
    return current
