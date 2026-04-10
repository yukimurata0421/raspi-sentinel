from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

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
    maintenance_suppress_until_ts: float | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: Mapping[str, object] | None) -> TargetState:
        if not isinstance(data, Mapping):
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
            "maintenance_suppress_until_ts",
        }
        extra = {k: v for k, v in data.items() if isinstance(k, str) and k not in known}
        last_action_raw = data.get("last_action")
        return cls(
            consecutive_failures=safe_int(data.get("consecutive_failures"), 0),
            last_status=str(data.get("last_status", "unknown") or "unknown"),
            last_reason=str(data.get("last_reason", "unknown") or "unknown"),
            last_action=(last_action_raw if isinstance(last_action_raw, str) else None),
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
            maintenance_suppress_until_ts=safe_float(data.get("maintenance_suppress_until_ts")),
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
        if self.maintenance_suppress_until_ts is not None:
            out["maintenance_suppress_until_ts"] = self.maintenance_suppress_until_ts
        return out

    def merge_into(self, raw: dict[str, Any]) -> None:
        """Replace *raw* contents with this model (preserves a single dict object identity)."""
        raw.clear()
        raw.update(self.to_dict())


@dataclass(slots=True)
class RebootRecord:
    ts: float
    target: str
    reason: str

    @classmethod
    def from_dict(cls, data: Mapping[str, object] | None) -> RebootRecord | None:
        if not isinstance(data, Mapping):
            return None
        ts = safe_float(data.get("ts"))
        target_raw = data.get("target")
        reason_raw = data.get("reason")
        if ts is None:
            return None
        target = target_raw if isinstance(target_raw, str) else "unknown"
        reason = reason_raw if isinstance(reason_raw, str) else ""
        return cls(ts=ts, target=target, reason=reason)

    def to_dict(self) -> dict[str, object]:
        return {
            "ts": self.ts,
            "target": self.target,
            "reason": self.reason,
        }


@dataclass(slots=True)
class FollowupRecord:
    due_ts: float
    created_ts: float
    initial_action: str
    initial_reason: str
    initial_consecutive_failures: int
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: Mapping[str, object] | None) -> FollowupRecord | None:
        if not isinstance(data, Mapping):
            return None
        due_ts = safe_float(data.get("due_ts"))
        created_ts = safe_float(data.get("created_ts"))
        initial_action_raw = data.get("initial_action")
        initial_reason_raw = data.get("initial_reason")
        if due_ts is None or created_ts is None or not isinstance(initial_action_raw, str):
            return None
        initial_reason = initial_reason_raw if isinstance(initial_reason_raw, str) else "unknown"
        known = {
            "due_ts",
            "created_ts",
            "initial_action",
            "initial_reason",
            "initial_consecutive_failures",
        }
        extra = {k: v for k, v in data.items() if isinstance(k, str) and k not in known}
        return cls(
            due_ts=due_ts,
            created_ts=created_ts,
            initial_action=initial_action_raw,
            initial_reason=initial_reason,
            initial_consecutive_failures=safe_int(data.get("initial_consecutive_failures"), 0),
            extra=extra,
        )

    def to_dict(self) -> dict[str, object]:
        out: dict[str, object] = dict(self.extra)
        out["due_ts"] = self.due_ts
        out["created_ts"] = self.created_ts
        out["initial_action"] = self.initial_action
        out["initial_reason"] = self.initial_reason
        out["initial_consecutive_failures"] = self.initial_consecutive_failures
        return out


@dataclass(slots=True)
class NotifyState:
    last_heartbeat_ts: float | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: Mapping[str, object] | None) -> NotifyState:
        if not isinstance(data, Mapping):
            return cls()
        known = {"last_heartbeat_ts"}
        extra = {k: v for k, v in data.items() if isinstance(k, str) and k not in known}
        return cls(
            last_heartbeat_ts=safe_float(data.get("last_heartbeat_ts")),
            extra=extra,
        )

    def to_dict(self) -> dict[str, object]:
        out: dict[str, object] = dict(self.extra)
        if self.last_heartbeat_ts is not None:
            out["last_heartbeat_ts"] = self.last_heartbeat_ts
        return out


@dataclass(slots=True)
class MonitorStatsRuntimeState:
    last_written_ts: float | None = None
    last_snapshot_signature: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: Mapping[str, object] | None) -> MonitorStatsRuntimeState:
        if not isinstance(data, Mapping):
            return cls()
        known = {"last_written_ts", "last_snapshot_signature"}
        extra = {k: v for k, v in data.items() if isinstance(k, str) and k not in known}
        signature_raw = data.get("last_snapshot_signature")
        return cls(
            last_written_ts=safe_float(data.get("last_written_ts")),
            last_snapshot_signature=(signature_raw if isinstance(signature_raw, str) else None),
            extra=extra,
        )

    def to_dict(self) -> dict[str, object]:
        out: dict[str, object] = dict(self.extra)
        if self.last_written_ts is not None:
            out["last_written_ts"] = self.last_written_ts
        if self.last_snapshot_signature is not None:
            out["last_snapshot_signature"] = self.last_snapshot_signature
        return out


@dataclass(slots=True)
class GlobalState:
    targets: dict[str, TargetState] = field(default_factory=dict)
    reboots: list[RebootRecord] = field(default_factory=list)
    followups: dict[str, FollowupRecord] = field(default_factory=dict)
    notify: NotifyState = field(default_factory=NotifyState)
    monitor_stats: MonitorStatsRuntimeState = field(default_factory=MonitorStatsRuntimeState)

    @classmethod
    def from_dict(cls, data: Mapping[str, object] | None) -> GlobalState:
        if not isinstance(data, Mapping):
            return cls()

        targets_raw = data.get("targets")
        targets: dict[str, TargetState] = {}
        if isinstance(targets_raw, Mapping):
            for name, target_raw in targets_raw.items():
                if not isinstance(name, str):
                    continue
                if isinstance(target_raw, Mapping):
                    targets[name] = TargetState.from_dict(target_raw)
                else:
                    targets[name] = TargetState()

        reboots_raw = data.get("reboots")
        reboots: list[RebootRecord] = []
        if isinstance(reboots_raw, list):
            for reboot_raw in reboots_raw:
                if isinstance(reboot_raw, Mapping):
                    parsed = RebootRecord.from_dict(reboot_raw)
                    if parsed is not None:
                        reboots.append(parsed)

        followups_raw = data.get("followups")
        followups: dict[str, FollowupRecord] = {}
        if isinstance(followups_raw, Mapping):
            for target_name, followup_raw in followups_raw.items():
                if not isinstance(target_name, str) or not isinstance(followup_raw, Mapping):
                    continue
                followup_record = FollowupRecord.from_dict(followup_raw)
                if followup_record is not None:
                    followups[target_name] = followup_record

        notify_raw = data.get("notify")
        notify = NotifyState.from_dict(notify_raw if isinstance(notify_raw, Mapping) else None)
        monitor_stats_raw = data.get("monitor_stats")
        monitor_stats = MonitorStatsRuntimeState.from_dict(
            monitor_stats_raw if isinstance(monitor_stats_raw, Mapping) else None
        )

        return cls(
            targets=targets,
            reboots=reboots,
            followups=followups,
            notify=notify,
            monitor_stats=monitor_stats,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "targets": {name: target.to_dict() for name, target in self.targets.items()},
            "reboots": [entry.to_dict() for entry in self.reboots],
            "followups": {
                target_name: followup.to_dict() for target_name, followup in self.followups.items()
            },
            "notify": self.notify.to_dict(),
            "monitor_stats": self.monitor_stats.to_dict(),
        }

    def ensure_target(self, target_name: str) -> TargetState:
        target = self.targets.get(target_name)
        if target is None:
            target = TargetState()
            self.targets[target_name] = target
        return target

    # Backward-compatible read-only mapping helpers for legacy tests/callers.
    def __getitem__(self, key: str) -> object:
        return self.to_dict()[key]

    def get(self, key: str, default: object = None) -> object:
        return self.to_dict().get(key, default)
