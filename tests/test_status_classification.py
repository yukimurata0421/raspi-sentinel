from __future__ import annotations

import json
from pathlib import Path

import pytest

from raspi_sentinel.checks import CheckFailure, CheckResult
from raspi_sentinel.policy import classify_target_policy
from raspi_sentinel.status_events import (
    apply_policy_to_result,
    classify_target_state,
    record_status_events,
)


def test_clock_skewed_maps_to_degraded() -> None:
    result = CheckResult(
        "t",
        True,
        [],
        observations={
            "clock_skew_detected": True,
            "ntp_sync_ok": True,
            "http_time_skew_sec": 400.0,
            "clock_skew_threshold_sec": 300.0,
        },
    )
    status, reason = classify_target_state(result, {})
    assert status == "degraded"
    assert reason == "clock_skewed"
    apply_policy_to_result(result, classify_target_policy(result, {}))
    assert result.healthy is False
    assert result.observations["policy_reason"] == "clock_skewed"


def test_dns_error_degraded_not_failed() -> None:
    result = CheckResult(
        "t",
        False,
        [CheckFailure("dependency_dns", "dns check failed")],
        observations={"gateway_ok": True},
    )
    status, reason = classify_target_state(result, {})
    assert status == "degraded"
    assert reason == "dns_error"


def test_stats_stale_is_degraded() -> None:
    result = CheckResult(
        "t",
        False,
        [CheckFailure("semantic_updated_at", "stale")],
        observations={},
    )
    status, reason = classify_target_state(result, {})
    assert status == "degraded"
    assert reason == "stats_stale"


def test_apply_policy_marks_ok_healthy() -> None:
    result = CheckResult("t", False, [], observations={})
    status, reason = classify_target_state(result, {})
    assert status == "ok"
    apply_policy_to_result(result, classify_target_policy(result, {}))
    assert result.healthy is True


def test_record_status_events_only_on_transition(tmp_path: Path) -> None:
    events = tmp_path / "e.jsonl"
    ts = 1_000_000.0
    target_state: dict = {}
    r = CheckResult("svc", False, [CheckFailure("command", "x")], observations={})
    record_status_events(
        events_file=events,
        target_state=target_state,
        target_name="svc",
        current_status="failed",
        current_reason="process_error",
        result=r,
        action="warn",
        now_ts=ts,
        max_file_bytes=0,
    )
    lines = events.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    first = json.loads(lines[0])
    assert first["from"] == "unknown"
    assert first["to"] == "failed"

    record_status_events(
        events_file=events,
        target_state=target_state,
        target_name="svc",
        current_status="failed",
        current_reason="process_error",
        result=r,
        action="warn",
        now_ts=ts + 1,
        max_file_bytes=0,
    )
    lines2 = events.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines2) == 1


def test_record_status_events_action_only_on_restart_reboot(tmp_path: Path) -> None:
    events = tmp_path / "e.jsonl"
    ts = 1_000_000.0
    target_state: dict = {"last_status": "failed", "last_reason": "process_error"}
    r = CheckResult("svc", False, [CheckFailure("command", "x")], observations={})
    record_status_events(
        events_file=events,
        target_state=target_state,
        target_name="svc",
        current_status="failed",
        current_reason="process_error",
        result=r,
        action="warn",
        now_ts=ts,
        max_file_bytes=0,
    )
    assert not events.exists()

    record_status_events(
        events_file=events,
        target_state=target_state,
        target_name="svc",
        current_status="failed",
        current_reason="process_error",
        result=r,
        action="restart",
        now_ts=ts,
        max_file_bytes=0,
    )
    lines = events.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    ev = json.loads(lines[0])
    assert ev.get("action") == "restart"


def test_service_active_requires_services(tmp_path: Path) -> None:
    from raspi_sentinel.config import load_config

    conf = tmp_path / "c.toml"
    conf.write_text(
        """
        [global]
        state_file = "/tmp/state.json"
        events_max_file_bytes = 0
        restart_threshold = 2
        reboot_threshold = 3
        restart_cooldown_sec = 10
        reboot_cooldown_sec = 20
        reboot_window_sec = 300
        max_reboots_in_window = 2
        min_uptime_for_reboot_sec = 60
        default_command_timeout_sec = 5
        loop_interval_sec = 30

        [notify.discord]
        enabled = false
        username = "raspi-sentinel"
        timeout_sec = 5
        followup_delay_sec = 300
        heartbeat_interval_sec = 0

        [[targets]]
        name = "demo"
        services = []
        service_active = true
        command = "true"
        """,
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="service_active=true"):
        load_config(conf)
