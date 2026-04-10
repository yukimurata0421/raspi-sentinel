from __future__ import annotations

from raspi_sentinel.state_models import GlobalState, TargetState


def test_target_state_round_trip_preserves_extra_and_clock_fields() -> None:
    raw = {
        "consecutive_failures": "2",
        "last_status": "degraded",
        "last_reason": "clock_frozen",
        "last_records_processed_total": "10",
        "records_stalled_cycles": "3",
        "clock_prev_wall_time_epoch": "1000.5",
        "clock_prev_monotonic_sec": "200.25",
        "consecutive_clock_freeze_count": "4",
        "clock_anomaly_consecutive": "5",
        "clock_last_reason": "clock_frozen_persistent",
        "extra_marker": "keep",
    }

    model = TargetState.from_dict(raw)
    assert model.consecutive_failures == 2
    assert model.last_records_processed_total == 10
    assert model.records_stalled_cycles == 3
    assert model.clock_prev_wall_time_epoch == 1000.5
    assert model.clock_prev_monotonic_sec == 200.25
    assert model.consecutive_clock_freeze_count == 4
    assert model.clock_anomaly_consecutive == 5
    assert model.clock_last_reason == "clock_frozen_persistent"

    out = model.to_dict()
    assert out["extra_marker"] == "keep"
    assert out["clock_prev_wall_time_epoch"] == 1000.5
    assert out["clock_prev_monotonic_sec"] == 200.25
    assert out["consecutive_clock_freeze_count"] == 4
    assert out["clock_anomaly_consecutive"] == 5


def test_target_state_merge_into_reuses_existing_dict_instance() -> None:
    raw = {"stale": "value"}
    model = TargetState(
        consecutive_failures=1,
        last_status="ok",
        last_reason="healthy",
        records_stalled_cycles=0,
        consecutive_clock_freeze_count=0,
        clock_anomaly_consecutive=0,
        extra={"custom": "x"},
    )

    before_id = id(raw)
    model.merge_into(raw)

    assert id(raw) == before_id
    assert raw["custom"] == "x"
    assert raw["consecutive_failures"] == 1
    assert "stale" not in raw


def test_global_state_migrates_legacy_shape_and_preserves_target_extra() -> None:
    legacy = {
        "targets": {
            "demo": {
                "consecutive_failures": "2",
                "last_status": "failed",
                "legacy_marker": "keep",
            }
        },
        "reboots": [
            {"ts": "10.5"},
            {"ts": "bad"},
        ],
    }

    model = GlobalState.from_dict(legacy)
    assert model.targets["demo"].consecutive_failures == 2
    assert model.targets["demo"].extra["legacy_marker"] == "keep"
    assert len(model.reboots) == 1
    assert model.reboots[0].target == "unknown"

    out = model.to_dict()
    assert out["targets"]["demo"]["legacy_marker"] == "keep"
    assert out["followups"] == {}
    assert out["notify"] == {}
    assert out["monitor_stats"] == {}


def test_global_state_parses_followup_notify_monitor_legacy_fields() -> None:
    legacy = {
        "followups": {
            "demo": {
                "due_ts": "120.0",
                "created_ts": "100.0",
                "initial_action": "warn",
                "initial_reason": 123,
                "initial_consecutive_failures": "4",
                "legacy_followup": "x",
            },
            "bad": {
                "due_ts": "bad",
                "created_ts": 100,
                "initial_action": "warn",
            },
        },
        "notify": {
            "last_heartbeat_ts": "80.5",
            "legacy_notify": "keep",
        },
        "monitor_stats": {
            "last_written_ts": "70.5",
            "last_snapshot_signature": 999,
            "legacy_monitor": "keep",
        },
    }

    model = GlobalState.from_dict(legacy)
    assert "demo" in model.followups
    assert "bad" not in model.followups
    assert model.followups["demo"].initial_reason == "unknown"
    assert model.followups["demo"].initial_consecutive_failures == 4
    assert model.followups["demo"].extra["legacy_followup"] == "x"
    assert model.notify.last_heartbeat_ts == 80.5
    assert model.notify.extra["legacy_notify"] == "keep"
    assert model.monitor_stats.last_written_ts == 70.5
    assert model.monitor_stats.last_snapshot_signature is None
    assert model.monitor_stats.extra["legacy_monitor"] == "keep"
