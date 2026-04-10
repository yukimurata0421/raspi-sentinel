from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .state_helpers import safe_float, safe_int, safe_optional_int


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
    clock_prev_wall_time_epoch: float | None = None
    clock_prev_monotonic_sec: float | None = None
    consecutive_clock_freeze_count: int = 0
    clock_anomaly_consecutive: int = 0
    clock_last_reason: str | None = None
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
            "clock_prev_wall_time_epoch",
            "clock_prev_monotonic_sec",
            "consecutive_clock_freeze_count",
            "clock_anomaly_consecutive",
            "clock_last_reason",
        }
        extra = {k: v for k, v in data.items() if k not in known}
        return cls(
            consecutive_failures=safe_int(data.get("consecutive_failures"), 0),
            last_status=str(data.get("last_status", "unknown") or "unknown"),
            last_reason=str(data.get("last_reason", "unknown") or "unknown"),
            last_action=data.get("last_action") if data.get("last_action") is not None else None,
            last_action_ts=safe_float(data.get("last_action_ts")),
            last_failure_ts=safe_float(data.get("last_failure_ts")),
            last_failure_reason=str(data.get("last_failure_reason", "") or ""),
            last_healthy_ts=safe_float(data.get("last_healthy_ts")),
            last_records_processed_total=safe_optional_int(
                data.get("last_records_processed_total")
            ),
            records_stalled_cycles=safe_int(data.get("records_stalled_cycles"), 0),
            clock_prev_wall_time_epoch=safe_float(data.get("clock_prev_wall_time_epoch")),
            clock_prev_monotonic_sec=safe_float(data.get("clock_prev_monotonic_sec")),
            consecutive_clock_freeze_count=safe_int(
                data.get("consecutive_clock_freeze_count"),
                0,
            ),
            clock_anomaly_consecutive=safe_int(data.get("clock_anomaly_consecutive"), 0),
            clock_last_reason=(
                str(data.get("clock_last_reason"))
                if data.get("clock_last_reason") is not None
                else None
            ),
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
        if self.clock_prev_wall_time_epoch is not None:
            out["clock_prev_wall_time_epoch"] = self.clock_prev_wall_time_epoch
        if self.clock_prev_monotonic_sec is not None:
            out["clock_prev_monotonic_sec"] = self.clock_prev_monotonic_sec
        out["consecutive_clock_freeze_count"] = self.consecutive_clock_freeze_count
        out["clock_anomaly_consecutive"] = self.clock_anomaly_consecutive
        if self.clock_last_reason is not None:
            out["clock_last_reason"] = self.clock_last_reason
        return out

    def merge_into(self, raw: dict[str, Any]) -> None:
        """Replace *raw* contents with this model (preserves a single dict object identity)."""
        raw.clear()
        raw.update(self.to_dict())
