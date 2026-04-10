from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class TargetState:
    """Per-target slice of ``state['targets'][name]`` (recovery + events + progress).

    Unknown keys (clock, maintenance, …) round-trip via ``extra``.
    Use :meth:`merge_into` to write back.
    """

    consecutive_failures: int = 0
    last_status: str = "unknown"
    last_reason: str = "unknown"
    last_action: str | None = None
    last_action_ts: float | None = None
    last_failure_ts: float | None = None
    last_failure_reason: str = ""
    last_healthy_ts: float | None = None
    last_records_processed_total: int | None = None
    records_stalled_cycles: int = 0
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> TargetState:
        if not isinstance(data, dict):
            return cls()
        known = {
            "consecutive_failures",
            "last_status",
            "last_reason",
            "last_action",
            "last_action_ts",
            "last_failure_ts",
            "last_failure_reason",
            "last_healthy_ts",
            "last_records_processed_total",
            "records_stalled_cycles",
        }
        extra = {k: v for k, v in data.items() if k not in known}
        return cls(
            consecutive_failures=int(data.get("consecutive_failures", 0) or 0),
            last_status=str(data.get("last_status", "unknown") or "unknown"),
            last_reason=str(data.get("last_reason", "unknown") or "unknown"),
            last_action=data.get("last_action") if data.get("last_action") is not None else None,
            last_action_ts=_optional_float(data.get("last_action_ts")),
            last_failure_ts=_optional_float(data.get("last_failure_ts")),
            last_failure_reason=str(data.get("last_failure_reason", "") or ""),
            last_healthy_ts=_optional_float(data.get("last_healthy_ts")),
            last_records_processed_total=_optional_int(data.get("last_records_processed_total")),
            records_stalled_cycles=int(data.get("records_stalled_cycles", 0) or 0),
            extra=extra,
        )

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = dict(self.extra)
        out["consecutive_failures"] = self.consecutive_failures
        out["last_status"] = self.last_status
        out["last_reason"] = self.last_reason
        if self.last_action is not None:
            out["last_action"] = self.last_action
        if self.last_action_ts is not None:
            out["last_action_ts"] = self.last_action_ts
        if self.last_failure_ts is not None:
            out["last_failure_ts"] = self.last_failure_ts
        if self.last_failure_reason:
            out["last_failure_reason"] = self.last_failure_reason
        if self.last_healthy_ts is not None:
            out["last_healthy_ts"] = self.last_healthy_ts
        if self.last_records_processed_total is not None:
            out["last_records_processed_total"] = self.last_records_processed_total
        out["records_stalled_cycles"] = self.records_stalled_cycles
        return out

    def merge_into(self, raw: dict[str, Any]) -> None:
        """Replace *raw* contents with this model (preserves a single dict object identity)."""
        raw.clear()
        raw.update(self.to_dict())


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
