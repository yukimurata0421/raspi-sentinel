from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

LOG = logging.getLogger(__name__)


def safe_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    return None


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


def safe_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def read_uptime_sec() -> float:
    try:
        with open("/proc/uptime", "r", encoding="utf-8") as fh:
            return float(fh.read().split()[0])
    except (OSError, ValueError, IndexError):
        return 0.0


def write_json_atomic(path: Path, payload: dict[str, Any], indent: int | None = 2) -> bool:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        kwargs: dict[str, Any] = {"sort_keys": True}
        if indent is not None:
            kwargs["indent"] = indent
        text = json.dumps(payload, **kwargs)
        fd = os.open(str(tmp_path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
        try:
            os.write(fd, (text + "\n").encode("utf-8"))
            os.fsync(fd)
        finally:
            os.close(fd)
        tmp_path.replace(path)
        dir_fd = os.open(str(path.parent), os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    except OSError as exc:
        LOG.error("failed to write JSON atomically %s: %s", path, exc)
        return False
    return True


def maybe_rotate_file(path: Path, max_bytes: int, backup_generations: int = 1) -> None:
    """Rotate ``path`` when size exceeds ``max_bytes``.

    Example with ``backup_generations=3``:
    ``events.jsonl`` -> ``events.jsonl.1`` and existing ``.1``/``.2`` shift to ``.2``/``.3``.
    """
    if max_bytes <= 0:
        return
    try:
        size = path.stat().st_size
    except OSError:
        return
    if size < max_bytes:
        return

    generations = max(1, backup_generations)
    try:
        oldest = path.with_name(f"{path.name}.{generations}")
        if oldest.exists():
            oldest.unlink()

        for idx in range(generations - 1, 0, -1):
            src = path.with_name(f"{path.name}.{idx}")
            dst = path.with_name(f"{path.name}.{idx + 1}")
            if src.exists():
                src.replace(dst)

        head = path.with_name(f"{path.name}.1")
        path.replace(head)
    except OSError as exc:
        LOG.warning("events rotation failed for %s: %s", path, exc)
