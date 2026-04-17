from __future__ import annotations

import time
from pathlib import Path

from .models import CheckFailure


def file_freshness_check(
    path: Path,
    max_age_sec: int,
    check_name: str,
    now_wall_ts: float | None = None,
) -> CheckFailure | None:
    try:
        stat = path.stat()
    except FileNotFoundError:
        return CheckFailure(check_name, f"file missing: {path}")
    except OSError as exc:
        return CheckFailure(check_name, f"cannot stat file {path}: {exc}")

    wall = time.time() if now_wall_ts is None else now_wall_ts
    age = wall - stat.st_mtime
    if age > max_age_sec:
        return CheckFailure(
            check_name,
            f"file stale: {path} age={age:.1f}s max={max_age_sec}s",
        )
    return None
