from __future__ import annotations

from raspi_sentinel.checks import CheckFailure, CheckResult
from raspi_sentinel.policy import (
    PROCESS_CHECK_NAMES,
    PolicySnapshot,
    classify_target_policy,
)
from raspi_sentinel.state_models import TargetState


def test_policy_snapshot_is_ok() -> None:
    p = PolicySnapshot("ok", "healthy")
    assert p.is_ok
    p2 = PolicySnapshot("degraded", "dns_error")
    assert not p2.is_ok


def test_process_check_names_covers_process_error_branch() -> None:
    assert "service_active" in PROCESS_CHECK_NAMES
    assert "command" in PROCESS_CHECK_NAMES


def test_classify_target_policy_returns_policy_snapshot() -> None:
    r = CheckResult(
        "t",
        False,
        [CheckFailure("dependency_dns", "x")],
        observations={"gateway_ok": True},
    )
    p = classify_target_policy(r, TargetState())
    assert isinstance(p, PolicySnapshot)
    assert p.status == "degraded"
    assert p.reason == "dns_error"


def test_clock_frozen_confirmed_is_failed() -> None:
    r = CheckResult(
        "t",
        False,
        [],
        observations={"clock_frozen_confirmed": True},
    )
    p = classify_target_policy(r, TargetState())
    assert p.status == "failed"
    assert p.reason == "clock_frozen_confirmed"


def test_http_probe_failed_is_degraded() -> None:
    r = CheckResult(
        "t",
        False,
        [],
        observations={"http_probe_ok": False},
    )
    p = classify_target_policy(r, TargetState())
    assert p.status == "degraded"
    assert p.reason == "http_error"


def test_time_sync_broken_skewed_when_skew_and_ntp_false() -> None:
    r = CheckResult(
        "t",
        False,
        [],
        observations={
            "clock_skew_detected": True,
            "ntp_sync_ok": False,
            "http_time_skew_sec": 400.0,
            "clock_skew_threshold_sec": 300.0,
        },
    )
    p = classify_target_policy(r, TargetState())
    assert p.status == "degraded"
    assert p.reason == "time_sync_broken_skewed"


def test_recovered_from_clock_skew_requires_previous_reason() -> None:
    r = CheckResult(
        "t",
        False,
        [],
        observations={
            "http_probe_ok": True,
            "http_time_skew_sec": 10.0,
            "clock_skew_threshold_sec": 300.0,
        },
    )
    p_no = classify_target_policy(r, TargetState())
    assert p_no.reason != "recovered_from_clock_skew"

    p_yes = classify_target_policy(
        r,
        TargetState(last_reason="clock_skewed"),
    )
    assert p_yes.status == "ok"
    assert p_yes.reason == "recovered_from_clock_skew"


def test_process_error_uses_process_check_names() -> None:
    r = CheckResult(
        "t",
        False,
        [CheckFailure("service_active", "inactive")],
        observations={},
    )
    p = classify_target_policy(r, TargetState())
    assert p.status == "failed"
    assert p.reason == "process_error"


def test_link_error_precedes_gateway_dns_errors() -> None:
    r = CheckResult(
        "t",
        False,
        [],
        observations={"link_ok": False, "gateway_ok": False, "dns_ok": False},
    )
    p = classify_target_policy(r, TargetState())
    assert p.status == "degraded"
    assert p.reason == "link_error"


def test_dns_server_error_when_dns_server_is_unreachable() -> None:
    r = CheckResult(
        "t",
        False,
        [],
        observations={"gateway_ok": True, "internet_ip_ok": True, "dns_server_reachable": False},
    )
    p = classify_target_policy(r, TargetState())
    assert p.status == "degraded"
    assert p.reason == "dns_server_error"


def test_network_probe_transient_failure_stays_ok() -> None:
    r = CheckResult(
        "t",
        False,
        [CheckFailure("dependency_dns", "x")],
        observations={
            "network_probe_enabled": True,
            "network_degraded_threshold": 2,
            "network_failed_threshold": 6,
            "dns_ok": False,
            "dns_fail_consecutive": 1,
            "internet_ip_ok": True,
        },
    )
    p = classify_target_policy(r, TargetState())
    assert p.status == "ok"
    assert p.reason == "transient_network_failure"


def test_network_probe_dns_error_after_consecutive_failures() -> None:
    r = CheckResult(
        "t",
        False,
        [],
        observations={
            "network_probe_enabled": True,
            "network_degraded_threshold": 2,
            "network_failed_threshold": 6,
            "dns_ok": False,
            "dns_fail_consecutive": 2,
            "internet_ip_ok": True,
        },
    )
    p = classify_target_policy(r, TargetState())
    assert p.status == "degraded"
    assert p.reason == "dns_error"


def test_network_probe_wan_error_split_from_gateway() -> None:
    r = CheckResult(
        "t",
        False,
        [],
        observations={
            "network_probe_enabled": True,
            "network_degraded_threshold": 2,
            "network_failed_threshold": 6,
            "gateway_ok": True,
            "internet_ip_ok": False,
            "internet_fail_consecutive": 3,
        },
    )
    p = classify_target_policy(r, TargetState())
    assert p.status == "degraded"
    assert p.reason == "wan_error"


def test_network_probe_link_failure_can_be_failed() -> None:
    r = CheckResult(
        "t",
        False,
        [],
        observations={
            "network_probe_enabled": True,
            "network_degraded_threshold": 2,
            "network_failed_threshold": 5,
            "link_ok": False,
            "link_fail_consecutive": 5,
        },
    )
    p = classify_target_policy(r, TargetState())
    assert p.status == "failed"
    assert p.reason == "link_error"


def test_network_probe_http_error_after_dns_ok() -> None:
    r = CheckResult(
        "t",
        False,
        [],
        observations={
            "network_probe_enabled": True,
            "network_degraded_threshold": 2,
            "network_failed_threshold": 6,
            "dns_ok": True,
            "http_probe_ok": False,
            "http_fail_consecutive": 2,
        },
    )
    p = classify_target_policy(r, TargetState())
    assert p.status == "degraded"
    assert p.reason == "http_error"


def test_network_probe_multi_factor_outage_becomes_failed() -> None:
    r = CheckResult(
        "t",
        False,
        [],
        observations={
            "network_probe_enabled": True,
            "network_degraded_threshold": 2,
            "network_failed_threshold": 4,
            "gateway_ok": False,
            "internet_ip_ok": False,
            "dns_ok": False,
            "http_probe_ok": False,
            "gateway_fail_consecutive": 4,
            "internet_fail_consecutive": 4,
            "dns_fail_consecutive": 4,
            "http_fail_consecutive": 4,
        },
    )
    p = classify_target_policy(r, TargetState())
    assert p.status == "failed"
    assert p.reason == "multi_factor_network_outage"


def test_external_internal_state_failed_maps_to_failed() -> None:
    r = CheckResult(
        "t",
        False,
        [CheckFailure("semantic_external_internal_state", "failed")],
        observations={"external_internal_state": "failed"},
    )
    p = classify_target_policy(r, TargetState())
    assert p.status == "failed"
    assert p.reason == "external_status_failed"


def test_external_progress_stall_maps_to_degraded() -> None:
    r = CheckResult(
        "t",
        False,
        [CheckFailure("semantic_external_last_progress_ts", "stale")],
        observations={},
    )
    p = classify_target_policy(r, TargetState())
    assert p.status == "degraded"
    assert p.reason == "external_progress_stall"


def test_clock_anomaly_takes_precedence_over_external_status_stale() -> None:
    r = CheckResult(
        "t",
        False,
        [CheckFailure("semantic_external_updated_at", "stale")],
        observations={"clock_jump_detected": True},
    )
    p = classify_target_policy(r, TargetState())
    assert p.status == "degraded"
    assert p.reason == "clock_jump"


def test_network_status_hysteresis_prevents_degraded_failed_ok_flap() -> None:
    transient = CheckResult(
        "t",
        False,
        [],
        observations={
            "network_probe_enabled": True,
            "network_degraded_threshold": 2,
            "network_failed_threshold": 4,
            "gateway_ok": False,
            "gateway_fail_consecutive": 1,
        },
    )
    p_transient = classify_target_policy(transient, TargetState())
    assert p_transient.status == "ok"
    assert p_transient.reason == "transient_network_failure"

    degraded = CheckResult(
        "t",
        False,
        [],
        observations={
            "network_probe_enabled": True,
            "network_degraded_threshold": 2,
            "network_failed_threshold": 4,
            "gateway_ok": False,
            "gateway_fail_consecutive": 2,
            "link_ok": True,
        },
    )
    p_degraded = classify_target_policy(degraded, TargetState())
    assert p_degraded.status == "degraded"
    assert p_degraded.reason == "gateway_error"

    recovered = CheckResult(
        "t",
        True,
        [],
        observations={
            "network_probe_enabled": True,
            "gateway_ok": True,
            "gateway_fail_consecutive": 0,
        },
    )
    p_recovered = classify_target_policy(recovered, TargetState())
    assert p_recovered.status == "ok"
    assert p_recovered.reason == "healthy"


def test_network_probe_failed_route_missing_reason() -> None:
    r = CheckResult(
        "t",
        False,
        [],
        observations={
            "network_probe_enabled": True,
            "network_failed_threshold": 3,
            "default_route_ok": False,
            "route_fail_consecutive": 3,
        },
    )
    p = classify_target_policy(r, TargetState())
    assert p.status == "failed"
    assert p.reason == "route_missing"


def test_network_probe_degraded_link_and_route_reasons() -> None:
    link = CheckResult(
        "t",
        False,
        [],
        observations={
            "network_probe_enabled": True,
            "network_degraded_threshold": 2,
            "link_ok": False,
            "link_fail_consecutive": 2,
        },
    )
    p_link = classify_target_policy(link, TargetState())
    assert p_link.status == "degraded"
    assert p_link.reason == "link_error"

    route = CheckResult(
        "t",
        False,
        [],
        observations={
            "network_probe_enabled": True,
            "network_degraded_threshold": 2,
            "default_route_ok": False,
            "route_fail_consecutive": 2,
        },
    )
    p_route = classify_target_policy(route, TargetState())
    assert p_route.status == "degraded"
    assert p_route.reason == "route_missing"


def test_network_probe_target_and_quality_reasons() -> None:
    target = CheckResult(
        "t",
        False,
        [],
        observations={
            "network_probe_enabled": True,
            "wan_vs_target_ok": False,
            "internet_ip_ok": True,
        },
    )
    p_target = classify_target_policy(target, TargetState())
    assert p_target.reason == "target_reachability_error"

    quality_cases = [
        ("gateway_latency_exceeded", "gateway_latency_high"),
        ("internet_latency_exceeded", "wan_latency_high"),
        ("gateway_loss_exceeded", "gateway_packet_loss"),
        ("internet_loss_exceeded", "wan_packet_loss"),
        ("dns_latency_exceeded", "dns_latency_high"),
        ("http_latency_exceeded", "http_latency_high"),
    ]
    for field_name, expected_reason in quality_cases:
        r = CheckResult(
            "t",
            False,
            [],
            observations={
                "network_probe_enabled": True,
                field_name: True,
            },
        )
        p = classify_target_policy(r, TargetState())
        assert p.status == "degraded"
        assert p.reason == expected_reason


def test_clock_and_recovery_reason_branches() -> None:
    frozen = CheckResult(
        "t",
        False,
        [],
        observations={"clock_frozen_detected": True, "consecutive_clock_freeze_count": 2},
    )
    assert classify_target_policy(frozen, TargetState()).reason == "clock_frozen_persistent"

    jump = CheckResult("t", False, [], observations={"clock_jump_detected": True})
    assert classify_target_policy(jump, TargetState()).reason == "clock_jump"

    sync_broken = CheckResult(
        "t",
        False,
        [],
        observations={
            "ntp_sync_ok": False,
            "http_time_skew_sec": 0.0,
            "clock_skew_threshold_sec": 300.0,
        },
    )
    assert classify_target_policy(sync_broken, TargetState()).reason == "time_sync_broken"

    insufficient = CheckResult(
        "t",
        True,
        [],
        observations={"insufficient_interval": True},
    )
    assert classify_target_policy(insufficient, TargetState()).reason == "insufficient_interval"

    recovered_jump = CheckResult("t", True, [], observations={})
    p_recovered = classify_target_policy(recovered_jump, TargetState(last_reason="clock_jump"))
    assert p_recovered.status == "ok"
    assert p_recovered.reason == "recovered_from_clock_jump"
