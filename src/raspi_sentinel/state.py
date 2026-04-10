from __future__ import annotations

import errno
import json
import logging
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

try:
    import fcntl
except ImportError:  # pragma: no cover - non-POSIX fallback
    fcntl = None  # type: ignore[assignment]

from .state_helpers import write_json_atomic
from .state_models import GlobalState, RebootRecord

LOG = logging.getLogger(__name__)


@dataclass(slots=True)
class StateLoadDiagnostics:
    used_default_state: bool = False
    state_corrupted: bool = False
    state_load_error: str | None = None
    corrupt_backup_path: Path | None = None

    @property
    def limited_mode(self) -> bool:
        return self.state_corrupted or self.state_load_error is not None


class StateStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.lock_path = path.with_suffix(path.suffix + ".lock")

    def load(self) -> GlobalState:
        state, _ = self.load_with_diagnostics()
        return state

    def _default_state(self) -> GlobalState:
        return GlobalState()

    def _quarantine_corrupt_state(self) -> Path | None:
        ts_label = datetime.now().strftime("%Y%m%dT%H%M%S")
        base = self.path.with_name(f"{self.path.name}.corrupt.{ts_label}")
        candidate = base
        for index in range(1, 100):
            if not candidate.exists():
                break
            candidate = self.path.with_name(f"{base.name}.{index}")
        try:
            self.path.replace(candidate)
            return candidate
        except OSError as exc:
            LOG.error("failed to quarantine corrupt state file %s: %s", self.path, exc)
            return None

    def _sanitize_loaded_state(self, data: dict[str, Any]) -> GlobalState:
        return GlobalState.from_dict(data)

    def load_with_diagnostics(self) -> tuple[GlobalState, StateLoadDiagnostics]:
        diagnostics = StateLoadDiagnostics()
        if not self.path.exists():
            return self._default_state(), diagnostics

        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            LOG.error("state file is invalid JSON (%s): %s", self.path, exc)
            diagnostics.used_default_state = True
            diagnostics.state_corrupted = True
            diagnostics.state_load_error = f"invalid JSON: {exc}"
            diagnostics.corrupt_backup_path = self._quarantine_corrupt_state()
            return self._default_state(), diagnostics
        except OSError as exc:
            LOG.error("cannot read state file %s: %s", self.path, exc)
            diagnostics.used_default_state = True
            diagnostics.state_load_error = f"read error: {exc}"
            return self._default_state(), diagnostics

        if not isinstance(data, dict):
            LOG.error("state file root must be object: %s", self.path)
            diagnostics.used_default_state = True
            diagnostics.state_corrupted = True
            diagnostics.state_load_error = "state JSON root is not an object"
            diagnostics.corrupt_backup_path = self._quarantine_corrupt_state()
            return self._default_state(), diagnostics

        return self._sanitize_loaded_state(data), diagnostics

    @contextmanager
    def exclusive_lock(self, timeout_sec: int = 5) -> Iterator[None]:
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
        state: GlobalState | dict[str, Any],
        max_file_bytes: int = 0,
        max_reboots_entries: int = 256,
    ) -> bool:
        if isinstance(state, GlobalState):
            state_model = state
            raw_state: dict[str, Any] | None = None
        else:
            state_model = GlobalState.from_dict(state)
            raw_state = state

        if max_reboots_entries > 0 and len(state_model.reboots) > max_reboots_entries:
            original_count = len(state_model.reboots)
            state_model.reboots = state_model.reboots[-max_reboots_entries:]
            LOG.warning(
                "state reboots list trimmed from %d to %d entries",
                original_count,
                max_reboots_entries,
            )

        payload = state_model.to_dict()
        if raw_state is not None:
            raw_state.clear()
            raw_state.update(payload)

        if max_file_bytes > 0:
            try:
                encoded = json.dumps(payload, sort_keys=True).encode("utf-8")
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

        return write_json_atomic(self.path, payload, indent=2)

    @staticmethod
    def append_reboot_record(
        state: GlobalState,
        *,
        now_ts: float,
        target: str,
        reason: str,
    ) -> None:
        state.reboots.append(
            RebootRecord(
                ts=now_ts,
                target=target,
                reason=reason,
            )
        )
