from __future__ import annotations

from raspi_sentinel.state_models import (
    FollowupRecord,
    GlobalState,
    NotifyDeliveryBacklog,
    NotifyState,
    RebootRecord,
    TargetState,
)


def test_state_model_non_mapping_inputs_and_optional_fields() -> None:
    assert TargetState.from_dict(None).consecutive_failures == 0
    assert RebootRecord.from_dict(None) is None
    assert FollowupRecord.from_dict(None) is None
    assert NotifyDeliveryBacklog.from_dict(None) is None

    target = TargetState(maintenance_suppress_until_ts=123.0)
    out = target.to_dict()
    assert out["maintenance_suppress_until_ts"] == 123.0


def test_followup_and_backlog_to_dict_and_filtering() -> None:
    followup = FollowupRecord(
        due_ts=10.0,
        created_ts=1.0,
        initial_action="warn",
        initial_reason="x",
        initial_consecutive_failures=2,
    )
    followup_out = followup.to_dict()
    assert followup_out["due_ts"] == 10.0
    assert followup_out["initial_action"] == "warn"

    assert (
        NotifyDeliveryBacklog.from_dict(
            {"first_failed_ts": None, "last_failed_ts": 1.0, "total_failures": 1}
        )
        is None
    )

    backlog = NotifyDeliveryBacklog.from_dict(
        {
            "first_failed_ts": 1.0,
            "last_failed_ts": 2.0,
            "total_failures": 0,
            "contexts": {"ok": 2, 1: 9, "zero": 0, "neg": -1},
            "extra_key": "v",
        }
    )
    assert backlog is not None
    assert backlog.total_failures == 1
    assert backlog.contexts == {"ok": 2}
    backlog_out = backlog.to_dict()
    assert backlog_out["first_failed_ts"] == 1.0
    assert backlog_out["contexts"] == {"ok": 2}


def test_notify_state_to_dict_includes_optional_fields() -> None:
    backlog = NotifyDeliveryBacklog(
        first_failed_ts=1.0,
        last_failed_ts=2.0,
        total_failures=3,
        contexts={"c": 1},
    )
    notify = NotifyState(last_heartbeat_ts=10.0, retry_due_ts=20.0, delivery_backlog=backlog)
    out = notify.to_dict()
    assert out["last_heartbeat_ts"] == 10.0
    assert out["retry_due_ts"] == 20.0
    assert "delivery_backlog" in out


def test_global_state_edge_parsing() -> None:
    state = GlobalState.from_dict(
        {
            "targets": {1: {"consecutive_failures": 1}, "demo": "bad"},
            "reboots": ["bad"],
            "followups": {1: {"due_ts": 1.0}, "demo": "bad"},
        }
    )
    assert "demo" in state.targets
    assert state.targets["demo"].consecutive_failures == 0
    assert state.reboots == []
    assert state.followups == {}

    assert state.followups == {}
    assert state.notify.to_dict() == {}
    assert state.monitor_stats.to_dict() == {}
