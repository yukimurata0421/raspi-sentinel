from __future__ import annotations

from typing import Any

from conftest import make_target


def target(**overrides: Any) -> Any:
    return make_target(**overrides)
