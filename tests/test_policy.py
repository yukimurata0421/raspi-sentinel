from __future__ import annotations

from raspi_sentinel.checks import CheckFailure, CheckResult
from raspi_sentinel.policy import (
    PROCESS_CHECK_NAMES,
    PolicySnapshot,
    classify_target_policy,
)


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
    p = classify_target_policy(r, {})
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
    p = classify_target_policy(r, {})
    assert p.status == "failed"
    assert p.reason == "clock_frozen_confirmed"


def test_http_probe_failed_is_degraded() -> None:
    r = CheckResult(
        "t",
        False,
        [],
        observations={"http_probe_ok": False},
    )
    p = classify_target_policy(r, {})
    assert p.status == "degraded"
    assert p.reason == "http_probe_failed"


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
    p = classify_target_policy(r, {})
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
    p_no = classify_target_policy(r, {})
    assert p_no.reason != "recovered_from_clock_skew"

    p_yes = classify_target_policy(
        r,
        {"last_reason": "clock_skewed"},
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
    p = classify_target_policy(r, {})
    assert p.status == "failed"
    assert p.reason == "process_error"
