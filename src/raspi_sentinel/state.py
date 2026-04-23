from __future__ import annotations

import errno
import json
import logging
import time
from contextlib import ExitStack, contextmanager
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


def ensure_directory(path: Path, mode: int = 0o755) -> bool:
    """Ensure a directory exists, and set mode when newly created."""
    try:
        created = False
        if not path.exists():
            path.mkdir(parents=True, exist_ok=True)
            created = True
        if created:
            path.chmod(mode)
    except OSError as exc:
        LOG.error("failed to prepare directory %s: %s", path, exc)
        return False
    return True


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
        ensure_directory(self.path.parent)
        ensure_directory(self.lock_path.parent)

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


class TieredStateStore:
    """Store state in volatile and optional durable tiers.

    - volatile: fast-changing runtime fields (failure counters, monitor stats, etc.)
    - durable: reboot-sensitive fields (reboot history, follow-up schedule, notify backlog)
    """

    def __init__(
        self,
        volatile_path: Path,
        durable_path: Path | None = None,
        durable_fields: tuple[str, ...] = (),
    ) -> None:
        self.volatile_store = StateStore(volatile_path)
        self.durable_store = StateStore(durable_path) if durable_path is not None else None
        self.durable_fields = tuple(dict.fromkeys(durable_fields))

    @property
    def _tiered_enabled(self) -> bool:
        return self.durable_store is not None and bool(self.durable_fields)

    @property
    def _lock_stores(self) -> list[StateStore]:
        stores = [self.volatile_store]
        if self.durable_store is not None:
            stores.append(self.durable_store)
        return sorted(stores, key=lambda store: str(store.lock_path))

    def _merge_state_payloads(
        self,
        volatile_data: dict[str, Any],
        durable_data: dict[str, Any],
    ) -> dict[str, Any]:
        merged = dict(volatile_data)
        if "reboot_history" in self.durable_fields and "reboots" in durable_data:
            merged["reboots"] = durable_data.get("reboots", [])
        if "followup_schedule" in self.durable_fields and "followups" in durable_data:
            merged["followups"] = durable_data.get("followups", {})
        if "notify_backlog" in self.durable_fields:
            durable_notify_raw = durable_data.get("notify")
            volatile_notify_raw = merged.get("notify")
            durable_notify = durable_notify_raw if isinstance(durable_notify_raw, dict) else {}
            volatile_notify = volatile_notify_raw if isinstance(volatile_notify_raw, dict) else {}
            if durable_notify or volatile_notify:
                merged_notify = dict(volatile_notify)
                for key in ("delivery_backlog", "retry_due_ts"):
                    if key in durable_notify:
                        merged_notify[key] = durable_notify[key]
                    else:
                        merged_notify.pop(key, None)
                merged["notify"] = merged_notify
        return merged

    def _split_payloads(self, state: GlobalState) -> tuple[dict[str, Any], dict[str, Any]]:
        volatile_payload = state.to_dict()
        durable_payload: dict[str, Any] = {}

        if "reboot_history" in self.durable_fields:
            reboots_raw = volatile_payload.get("reboots")
            durable_payload["reboots"] = list(reboots_raw) if isinstance(reboots_raw, list) else []
            volatile_payload.pop("reboots", None)
        if "followup_schedule" in self.durable_fields:
            followups_raw = volatile_payload.get("followups")
            durable_payload["followups"] = (
                dict(followups_raw) if isinstance(followups_raw, dict) else {}
            )
            volatile_payload.pop("followups", None)
        if "notify_backlog" in self.durable_fields:
            volatile_notify_raw = volatile_payload.get("notify")
            volatile_notify = volatile_notify_raw if isinstance(volatile_notify_raw, dict) else {}
            durable_notify: dict[str, Any] = {}
            if "delivery_backlog" in volatile_notify:
                durable_notify["delivery_backlog"] = volatile_notify["delivery_backlog"]
                volatile_notify.pop("delivery_backlog", None)
            if "retry_due_ts" in volatile_notify:
                durable_notify["retry_due_ts"] = volatile_notify["retry_due_ts"]
                volatile_notify.pop("retry_due_ts", None)
            if durable_notify:
                durable_payload["notify"] = durable_notify
            if volatile_notify:
                volatile_payload["notify"] = volatile_notify
            else:
                volatile_payload.pop("notify", None)

        return volatile_payload, durable_payload

    def load(self) -> GlobalState:
        state, _ = self.load_with_diagnostics()
        return state

    def load_with_diagnostics(self) -> tuple[GlobalState, StateLoadDiagnostics]:
        volatile_state, volatile_diag = self.volatile_store.load_with_diagnostics()
        if not self._tiered_enabled:
            return volatile_state, volatile_diag

        assert self.durable_store is not None
        durable_state, durable_diag = self.durable_store.load_with_diagnostics()
        merged_payload = self._merge_state_payloads(
            volatile_state.to_dict(),
            durable_state.to_dict(),
        )
        merged_model = GlobalState.from_dict(merged_payload)

        merged_diag = StateLoadDiagnostics(
            used_default_state=volatile_diag.used_default_state or durable_diag.used_default_state,
            state_corrupted=volatile_diag.state_corrupted or durable_diag.state_corrupted,
        )
        issues: list[str] = []
        if volatile_diag.state_load_error:
            issues.append(f"volatile: {volatile_diag.state_load_error}")
        if durable_diag.state_load_error:
            issues.append(f"durable: {durable_diag.state_load_error}")
        if issues:
            merged_diag.state_load_error = "; ".join(issues)
        merged_diag.corrupt_backup_path = (
            durable_diag.corrupt_backup_path or volatile_diag.corrupt_backup_path
        )
        return merged_model, merged_diag

    @contextmanager
    def exclusive_lock(self, timeout_sec: int = 5) -> Iterator[None]:
        with ExitStack() as stack:
            for store in self._lock_stores:
                stack.enter_context(store.exclusive_lock(timeout_sec=timeout_sec))
            yield

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

        if not self._tiered_enabled:
            return self.volatile_store.save(
                state_model,
                max_file_bytes=max_file_bytes,
                max_reboots_entries=max_reboots_entries,
            )

        volatile_payload, durable_payload = self._split_payloads(state_model)

        volatile_ok = self._save_raw_payload(
            path=self.volatile_store.path,
            payload=volatile_payload,
            max_file_bytes=max_file_bytes,
        )
        if not volatile_ok:
            return False

        durable_ok = True
        if self.durable_store is not None:
            durable_ok = self._save_raw_payload(
                path=self.durable_store.path,
                payload=durable_payload,
                max_file_bytes=max_file_bytes,
            )
        if not durable_ok:
            return False

        if raw_state is not None:
            raw_state.clear()
            raw_state.update(state_model.to_dict())
        return True

    def _save_raw_payload(
        self,
        *,
        path: Path,
        payload: dict[str, Any],
        max_file_bytes: int,
    ) -> bool:
        if max_file_bytes > 0:
            try:
                encoded = json.dumps(payload, sort_keys=True).encode("utf-8")
            except (TypeError, ValueError) as exc:
                LOG.error("cannot serialize state for size check: %s", exc)
                return False
            if len(encoded) > max_file_bytes:
                LOG.error(
                    "state file write blocked by size guard: size=%d max=%d path=%s",
                    len(encoded),
                    max_file_bytes,
                    path,
                )
                return False
        # Both volatile and durable tier writes go through the same
        # temp-file + rename atomic helper.
        return write_json_atomic(path, payload, indent=2)
