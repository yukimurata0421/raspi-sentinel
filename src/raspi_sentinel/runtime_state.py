from __future__ import annotations

from typing import Any

from .state_helpers import safe_int as safe_int
from .state_helpers import safe_optional_int as safe_optional_int


def target_state(state: dict[str, Any], target_name: str) -> dict[str, Any]:
    targets = state.setdefault("targets", {})
    current = targets.get(target_name)
    if not isinstance(current, dict):
        current = {}
        targets[target_name] = current
    return current
