from __future__ import annotations

import copy
import errno
import json
import logging
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any

try:
    import fcntl
except ImportError:  # pragma: no cover - non-POSIX fallback
    fcntl = None  # type: ignore[assignment]

from .state_helpers import write_json_atomic

LOG = logging.getLogger(__name__)


DEFAULT_STATE: dict[str, Any] = {
    "targets": {},
    "reboots": [],
    "followups": {},
    "notify": {},
    "monitor_stats": {},
}


class StateStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.lock_path = path.with_suffix(path.suffix + ".lock")

    def load(self) -> dict[str, Any]:
        if not self.path.exists():
            return copy.deepcopy(DEFAULT_STATE)

        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            LOG.error("state file is invalid JSON (%s): %s", self.path, exc)
            return copy.deepcopy(DEFAULT_STATE)
        except OSError as exc:
            LOG.error("cannot read state file %s: %s", self.path, exc)
            return copy.deepcopy(DEFAULT_STATE)

        if not isinstance(data, dict):
            LOG.error("state file root must be object: %s", self.path)
            return copy.deepcopy(DEFAULT_STATE)

        targets = data.get("targets")
        reboots = data.get("reboots")
        followups = data.get("followups")
        notify = data.get("notify")
        monitor_stats = data.get("monitor_stats")
        if not isinstance(targets, dict):
            targets = {}
        if not isinstance(reboots, list):
            reboots = []
        if not isinstance(followups, dict):
            followups = {}
        if not isinstance(notify, dict):
            notify = {}
        if not isinstance(monitor_stats, dict):
            monitor_stats = {}

        return {
            "targets": targets,
            "reboots": reboots,
            "followups": followups,
            "notify": notify,
            "monitor_stats": monitor_stats,
        }

    @contextmanager
    def exclusive_lock(self, timeout_sec: int = 5) -> Any:
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        with self.lock_path.open("a+", encoding="utf-8") as lock_fh:
            if fcntl is not None:
                deadline = time.monotonic() + max(1, timeout_sec)
                while True:
                    try:
                        fcntl.flock(lock_fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                        break
                    except OSError as exc:
                        if exc.errno not in (errno.EACCES, errno.EAGAIN):
                            raise
                        if time.monotonic() >= deadline:
                            raise TimeoutError(
                                f"state lock timeout after {timeout_sec}s: {self.lock_path}"
                            ) from exc
                        time.sleep(0.1)
            try:
                yield
            finally:
                if fcntl is not None:
                    fcntl.flock(lock_fh.fileno(), fcntl.LOCK_UN)

    def save(
        self,
        state: dict[str, Any],
        max_file_bytes: int = 0,
        max_reboots_entries: int = 256,
    ) -> bool:
        reboots = state.get("reboots")
        if (
            isinstance(reboots, list)
            and max_reboots_entries > 0
            and len(reboots) > max_reboots_entries
        ):
            state["reboots"] = reboots[-max_reboots_entries:]
            LOG.warning(
                "state reboots list trimmed from %d to %d entries",
                len(reboots),
                max_reboots_entries,
            )

        if max_file_bytes > 0:
            try:
                encoded = json.dumps(state, sort_keys=True).encode("utf-8")
            except (TypeError, ValueError) as exc:
                LOG.error("cannot serialize state for size check: %s", exc)
                return False
            size_bytes = len(encoded)
            if size_bytes > max_file_bytes:
                LOG.error(
                    "state file write blocked by size guard: size=%d max=%d path=%s",
                    size_bytes,
                    max_file_bytes,
                    self.path,
                )
                return False

        return write_json_atomic(self.path, state, indent=2)
