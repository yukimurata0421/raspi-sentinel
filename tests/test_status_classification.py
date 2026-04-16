from __future__ import annotations

import json
from pathlib import Path

import pytest

from raspi_sentinel.checks import CheckFailure, CheckResult
from raspi_sentinel.policy import classify_target_policy
from raspi_sentinel.state_models import TargetState
from raspi_sentinel.status_events import (
    append_event,
    apply_policy_to_result,
    build_event_evidence,
    classify_target_state,
    record_notify_failure_event,
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
    status, reason = classify_target_state(result, TargetState())
    assert status == "degraded"
    assert reason == "clock_skewed"
    apply_policy_to_result(result, classify_target_policy(result, TargetState()))
    assert result.healthy is False
    assert result.observations["policy_reason"] == "clock_skewed"
    assert result.observations["policy_subreason"] is None


def test_dns_error_degraded_not_failed() -> None:
    result = CheckResult(
        "t",
        False,
        [CheckFailure("dependency_dns", "dns check failed")],
        observations={"gateway_ok": True},
    )
    status, reason = classify_target_state(result, TargetState())
    assert status == "degraded"
    assert reason == "dns_error"


def test_stats_stale_is_degraded() -> None:
    result = CheckResult(
        "t",
        False,
        [CheckFailure("semantic_updated_at", "stale")],
        observations={},
    )
    status, reason = classify_target_state(result, TargetState())
    assert status == "degraded"
    assert reason == "stats_stale"


def test_apply_policy_marks_ok_healthy() -> None:
    result = CheckResult("t", False, [], observations={})
    status, reason = classify_target_state(result, TargetState())
    assert status == "ok"
    apply_policy_to_result(result, classify_target_policy(result, TargetState()))
    assert result.healthy is True


def test_record_status_events_only_on_transition(tmp_path: Path) -> None:
    events = tmp_path / "e.jsonl"
    ts = 1_000_000.0
    target_state = TargetState()
    r = CheckResult("svc", False, [CheckFailure("command", "x")], observations={})
    record_status_events(
        events_file=events,
        target_state=target_state,
        target_name="svc",
        current_status="failed",
        current_reason="process_error",
        current_subreason="all_targets_failed",
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
    assert first["subreason"] == "all_targets_failed"

    record_status_events(
        events_file=events,
        target_state=target_state,
        target_name="svc",
        current_status="failed",
        current_reason="process_error",
        current_subreason="all_targets_failed",
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
    target_state = TargetState(last_status="failed", last_reason="process_error")
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


def test_record_status_events_preserves_null_vs_false_evidence(tmp_path: Path) -> None:
    events = tmp_path / "e.jsonl"
    target_state = TargetState()
    result = CheckResult(
        "network_uplink",
        False,
        [],
        observations={
            "network_probe_enabled": True,
            "link_ok": None,
            "gateway_ok": False,
            "dns_ok": None,
            "http_probe_ok": False,
            "gateway_latency_ms": 123.4,
            "dns_error_kind": None,
        },
    )
    record_status_events(
        events_file=events,
        target_state=target_state,
        target_name="network_uplink",
        current_status="degraded",
        current_reason="gateway_error",
        result=result,
        action="warn",
        now_ts=1_000_000.0,
        max_file_bytes=0,
    )
    line = events.read_text(encoding="utf-8").strip().splitlines()[0]
    payload = json.loads(line)
    assert "link_ok" in payload and payload["link_ok"] is None
    assert "dns_ok" in payload and payload["dns_ok"] is None
    assert payload["gateway_ok"] is False
    assert payload["http_probe_ok"] is False


def test_events_include_neighbor_and_arp_gateway_evidence(tmp_path: Path) -> None:
    events = tmp_path / "e.jsonl"
    target_state = TargetState()
    result = CheckResult(
        "network_uplink",
        False,
        [],
        observations={
            "neighbor_resolved": False,
            "arp_gateway_ok": False,
            "gateway_ip": "192.168.1.1",
            "default_route_iface": "wlan0",
            "route_table_snapshot": "default via 192.168.1.1 dev wlan0",
        },
    )
    record_status_events(
        events_file=events,
        target_state=target_state,
        target_name="network_uplink",
        current_status="degraded",
        current_reason="gateway_error",
        result=result,
        action="warn",
        now_ts=1_000_000.0,
        max_file_bytes=0,
    )
    payload = json.loads(events.read_text(encoding="utf-8").strip())
    assert payload["neighbor_resolved"] is False
    assert payload["arp_gateway_ok"] is False
    assert payload["gateway_ip"] == "192.168.1.1"
    assert payload["default_route_iface"] == "wlan0"
    assert "route_table_snapshot" in payload


def test_record_notify_failure_event_appends_line(tmp_path: Path) -> None:
    events = tmp_path / "events.jsonl"
    record_notify_failure_event(
        events_file=events,
        max_file_bytes=0,
        backup_generations=1,
        context="discord:webhook",
        now_ts=1_000_000.0,
    )
    payload = json.loads(events.read_text(encoding="utf-8").strip())
    assert payload["kind"] == "notify_delivery_failed"
    assert payload["context"] == "discord:webhook"


def test_append_event_oserror_is_handled(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    events = tmp_path / "events.jsonl"

    class BrokenFile:
        def __enter__(self) -> "BrokenFile":
            raise OSError("disk full")

        def __exit__(self, exc_type: object, exc: object, tb: object) -> bool:
            return False

    monkeypatch.setattr(Path, "open", lambda self, *args, **kwargs: BrokenFile(), raising=False)
    append_event(events, {"k": "v"}, max_file_bytes=0, backup_generations=1)
    assert not events.exists()


def test_build_event_evidence_includes_freeze_counter() -> None:
    result = CheckResult(
        "clock",
        False,
        [],
        observations={
            "consecutive_clock_freeze_count": 4,
            "link_ok": None,
        },
    )
    payload = build_event_evidence(result)
    assert payload["consecutive_clock_freeze_count"] == 4
    assert payload["link_ok"] is None


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
