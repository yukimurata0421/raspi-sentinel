from __future__ import annotations

import subprocess
from typing import Any

from conftest import make_global_config, make_target

from raspi_sentinel.checks import CheckFailure, CheckResult
from raspi_sentinel.recovery import (
    apply_recovery,
    network_only_failures_can_reboot,
    network_only_failures_excluded_from_reboot,
)
from raspi_sentinel.state_models import GlobalState


def _global(**overrides: Any) -> Any:
    defaults = {
        "restart_threshold": 1,
        "reboot_threshold": 1,
        "restart_cooldown_sec": 0,
        "reboot_cooldown_sec": 0,
        "reboot_window_sec": 3600,
        "max_reboots_in_window": 2,
        "min_uptime_for_reboot_sec": 0,
        "default_command_timeout_sec": 5,
        "loop_interval_sec": 30,
    }
    defaults.update(overrides)
    return make_global_config(**defaults)


def test_systemd_failure_triggers_restart(monkeypatch: Any) -> None:
    calls: list[list[str]] = []

    def fake_run(cmd: list[str], **_: Any) -> Any:
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr("raspi_sentinel.recovery.subprocess.run", fake_run)

    result = CheckResult(
        target="demo",
        healthy=False,
        failures=[CheckFailure("service_active", "service not active")],
    )
    state = GlobalState()
    outcome = apply_recovery(
        target=make_target(
            services=["demo.service"],
            service_active=True,
            restart_threshold=1,
            reboot_threshold=5,
        ),
        check_result=result,
        global_config=_global(),
        state=state,
        dry_run=False,
    )
    assert outcome.action == "restart"
    assert not outcome.requested_reboot
    assert calls and calls[0] == ["systemctl", "restart", "demo.service"]


def test_dns_only_failure_does_not_reboot_even_when_threshold_reached() -> None:
    result = CheckResult(
        target="demo",
        healthy=False,
        failures=[CheckFailure("dependency_dns", "dns check failed")],
    )
    state = GlobalState()
    outcome = apply_recovery(
        target=make_target(restart_threshold=1, reboot_threshold=1),
        check_result=result,
        global_config=_global(),
        state=state,
        dry_run=True,
    )
    assert outcome.action == "warn"
    assert not outcome.requested_reboot
    assert state.reboots == []


def test_gateway_failure_does_not_request_reboot_even_when_policy_failed(monkeypatch: Any) -> None:
    monkeypatch.setattr("raspi_sentinel.recovery.read_uptime_sec", lambda: 999999.0)
    result = CheckResult(
        target="demo",
        healthy=False,
        failures=[CheckFailure("dependency_gateway", "gateway failed")],
        observations={"policy_status": "failed"},
    )
    state = GlobalState()
    outcome = apply_recovery(
        target=make_target(restart_threshold=1, reboot_threshold=1),
        check_result=result,
        global_config=_global(),
        state=state,
        dry_run=True,
    )
    assert outcome.action == "warn"
    assert not outcome.requested_reboot


def test_recovery_reboot_requires_policy_failed_for_network_uplink(monkeypatch: Any) -> None:
    monkeypatch.setattr("raspi_sentinel.recovery.read_uptime_sec", lambda: 999999.0)
    result = CheckResult(
        target="network_uplink",
        healthy=False,
        failures=[CheckFailure("dependency_gateway", "gateway failed")],
        observations={"policy_status": "degraded"},
    )
    state = GlobalState()
    outcome = apply_recovery(
        target=make_target(name="network_uplink", restart_threshold=1, reboot_threshold=1),
        check_result=result,
        global_config=_global(),
        state=state,
        dry_run=True,
    )
    assert outcome.action == "warn"
    assert not outcome.requested_reboot

    failed_result = CheckResult(
        target="network_uplink",
        healthy=False,
        failures=[CheckFailure("dependency_gateway", "gateway failed")],
        observations={"policy_status": "failed"},
    )
    outcome_failed = apply_recovery(
        target=make_target(name="network_uplink", restart_threshold=1, reboot_threshold=1),
        check_result=failed_result,
        global_config=_global(),
        state=GlobalState(),
        dry_run=True,
    )
    assert outcome_failed.action == "warn"
    assert not outcome_failed.requested_reboot


def test_external_status_failed_integrates_restart_then_reboot(monkeypatch: Any) -> None:
    monkeypatch.setattr("raspi_sentinel.recovery.read_uptime_sec", lambda: 999999.0)
    calls: list[list[str]] = []

    def fake_run(cmd: list[str], **_: Any) -> Any:
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr("raspi_sentinel.recovery.subprocess.run", fake_run)

    result = CheckResult(
        target="demo",
        healthy=False,
        failures=[CheckFailure("semantic_external_internal_state", "failed")],
        observations={"policy_status": "failed"},
    )
    state = GlobalState()
    target = make_target(
        services=["demo.service"],
        service_active=True,
        restart_threshold=1,
        reboot_threshold=2,
    )

    first = apply_recovery(
        target=target,
        check_result=result,
        global_config=_global(),
        state=state,
        dry_run=False,
    )
    assert first.action == "restart"
    assert not first.requested_reboot
    assert calls and calls[0] == ["systemctl", "restart", "demo.service"]

    second = apply_recovery(
        target=target,
        check_result=result,
        global_config=_global(),
        state=state,
        dry_run=False,
    )
    assert second.action == "reboot"
    assert second.requested_reboot


def test_reboot_is_suppressed_immediately_after_restart(monkeypatch: Any) -> None:
    monkeypatch.setattr("raspi_sentinel.recovery.read_uptime_sec", lambda: 999999.0)
    result = CheckResult(
        target="demo",
        healthy=False,
        failures=[CheckFailure("semantic_external_internal_state", "failed")],
        observations={"policy_status": "failed"},
    )
    state = GlobalState.from_dict(
        {
            "targets": {
                "demo": {
                    "consecutive_failures": 1,
                    "last_action": "restart",
                    "last_action_ts": 1000.0,
                }
            }
        }
    )
    global_cfg = _global(restart_cooldown_sec=120)
    outcome = apply_recovery(
        target=make_target(restart_threshold=1, reboot_threshold=1),
        check_result=result,
        global_config=global_cfg,
        state=state,
        dry_run=True,
        now_ts=1060.0,
    )
    assert outcome.action == "warn"
    assert not outcome.requested_reboot


def test_network_only_failure_reboot_flag_is_disabled_by_policy() -> None:
    assert network_only_failures_excluded_from_reboot() is True
    assert network_only_failures_can_reboot() is False
