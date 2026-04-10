from __future__ import annotations

from raspi_sentinel.checks import CheckFailure, CheckResult
from raspi_sentinel.policy import PolicySnapshot, classify_target_policy


def test_policy_snapshot_is_ok() -> None:
    p = PolicySnapshot("ok", "healthy")
    assert p.is_ok
    p2 = PolicySnapshot("degraded", "dns_error")
    assert not p2.is_ok


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
