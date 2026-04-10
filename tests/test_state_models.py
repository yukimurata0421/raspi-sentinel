from __future__ import annotations

from raspi_sentinel.state_models import TargetState


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
