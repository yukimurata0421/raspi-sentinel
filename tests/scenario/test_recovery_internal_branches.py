from __future__ import annotations

import subprocess
from typing import Any

from conftest import make_global_config, make_target

from raspi_sentinel import recovery
from raspi_sentinel.checks import CheckFailure, CheckResult
from raspi_sentinel.state_models import GlobalState, RebootRecord


def _global(**overrides: Any) -> Any:
    defaults = {
        "restart_threshold": 2,
        "reboot_threshold": 3,
        "restart_cooldown_sec": 30,
        "reboot_cooldown_sec": 30,
        "reboot_window_sec": 300,
        "max_reboots_in_window": 2,
        "min_uptime_for_reboot_sec": 60,
        "default_command_timeout_sec": 5,
        "loop_interval_sec": 30,
    }
    defaults.update(overrides)
    return make_global_config(**defaults)


def test_can_reboot_guard_paths(monkeypatch: Any) -> None:
    monkeypatch.setattr(recovery, "read_uptime_sec", lambda: 10.0)
    ok, reason = recovery._can_reboot(_global(min_uptime_for_reboot_sec=60), GlobalState(), 100.0)
    assert not ok and "uptime guard" in reason

    monkeypatch.setattr(recovery, "read_uptime_sec", lambda: 1000.0)
    state = GlobalState(reboots=[RebootRecord(ts=95.0, target="demo", reason="test")])
    ok, reason = recovery._can_reboot(_global(reboot_cooldown_sec=20), state, 100.0)
    assert not ok and "cooldown" in reason

    state = GlobalState(
        reboots=[
            RebootRecord(ts=80.0, target="demo", reason="a"),
            RebootRecord(ts=90.0, target="demo", reason="b"),
        ]
    )
    ok, reason = recovery._can_reboot(
        _global(reboot_cooldown_sec=0, max_reboots_in_window=2), state, 100.0
    )
    assert not ok and "window cap" in reason

    state = GlobalState(reboots=[RebootRecord(ts=0.0, target="demo", reason="old")])
    ok, reason = recovery._can_reboot(_global(reboot_window_sec=10), state, 100.0)
    assert ok and reason == "allowed"
    assert state.reboots == []


def test_read_uptime_sec_exception_branch(monkeypatch: Any) -> None:
    def raise_open(*args: Any, **kwargs: Any) -> Any:
        raise OSError("no proc")

    monkeypatch.setattr("builtins.open", raise_open)
    from raspi_sentinel.state_helpers import read_uptime_sec

    assert read_uptime_sec() == 0.0


def test_can_reboot_guard_boundary_values(monkeypatch: Any) -> None:
    monkeypatch.setattr(recovery, "read_uptime_sec", lambda: 60.0)
    edge_state = GlobalState(reboots=[RebootRecord(ts=80.0, target="demo", reason="edge")])
    ok, reason = recovery._can_reboot(
        _global(
            min_uptime_for_reboot_sec=60,
            reboot_cooldown_sec=20,
            reboot_window_sec=20,
            max_reboots_in_window=3,
        ),
        edge_state,
        100.0,
    )
    assert ok and reason == "allowed"
    assert edge_state.reboots[0].ts == 80.0

    cap_state = GlobalState(
        reboots=[
            RebootRecord(ts=81.0, target="demo", reason="a"),
            RebootRecord(ts=99.0, target="demo", reason="b"),
        ]
    )
    ok, reason = recovery._can_reboot(
        _global(
            min_uptime_for_reboot_sec=0,
            reboot_cooldown_sec=0,
            reboot_window_sec=20,
            max_reboots_in_window=2,
        ),
        cap_state,
        100.0,
    )
    assert not ok and "window cap" in reason


def test_restart_services_branches(monkeypatch: Any) -> None:
    assert not recovery._restart_services([], dry_run=True)
    assert recovery._restart_services(["svc"], dry_run=True)

    def timeout_run(*_: Any, **__: Any) -> Any:
        raise subprocess.TimeoutExpired(cmd="systemctl", timeout=30)

    monkeypatch.setattr(recovery.subprocess, "run", timeout_run)
    assert not recovery._restart_services(["svc"], dry_run=False)

    def os_run(*_: Any, **__: Any) -> Any:
        raise OSError("no systemctl")

    monkeypatch.setattr(recovery.subprocess, "run", os_run)
    assert not recovery._restart_services(["svc"], dry_run=False)

    def fail_run(*_: Any, **__: Any) -> Any:
        return subprocess.CompletedProcess(
            args=["systemctl"], returncode=1, stdout="", stderr="failed"
        )

    monkeypatch.setattr(recovery.subprocess, "run", fail_run)
    assert not recovery._restart_services(["svc"], dry_run=False)

    def ok_run(*_: Any, **__: Any) -> Any:
        return subprocess.CompletedProcess(args=["systemctl"], returncode=0, stdout="", stderr="")

    monkeypatch.setattr(recovery.subprocess, "run", ok_run)
    assert recovery._restart_services(["svc"], dry_run=False)


def test_trigger_reboot_branches(monkeypatch: Any) -> None:
    assert recovery._trigger_reboot(dry_run=True, reason="x")

    def timeout_run(*_: Any, **__: Any) -> Any:
        raise subprocess.TimeoutExpired(cmd="reboot", timeout=15)

    monkeypatch.setattr(recovery.subprocess, "run", timeout_run)
    assert not recovery._trigger_reboot(dry_run=False, reason="x")

    def os_run(*_: Any, **__: Any) -> Any:
        raise OSError("boom")

    monkeypatch.setattr(recovery.subprocess, "run", os_run)
    assert not recovery._trigger_reboot(dry_run=False, reason="x")

    def fail_run(*_: Any, **__: Any) -> Any:
        return subprocess.CompletedProcess(
            args=["systemctl"], returncode=1, stdout="", stderr="failed"
        )

    monkeypatch.setattr(recovery.subprocess, "run", fail_run)
    assert not recovery._trigger_reboot(dry_run=False, reason="x")

    def ok_run(*_: Any, **__: Any) -> Any:
        return subprocess.CompletedProcess(args=["systemctl"], returncode=0, stdout="", stderr="")

    monkeypatch.setattr(recovery.subprocess, "run", ok_run)
    assert recovery._trigger_reboot(dry_run=False, reason="x")


def test_apply_recovery_healthy_path_resets_counter() -> None:
    state = GlobalState.from_dict({"targets": {"demo": {"consecutive_failures": 3}}})
    outcome = recovery.apply_recovery(
        target=make_target(
            services=["demo.service"],
            service_active=True,
            restart_threshold=2,
            reboot_threshold=3,
        ),
        check_result=CheckResult(target="demo", healthy=True, failures=[]),
        global_config=_global(),
        state=state,
        dry_run=True,
    )
    assert outcome.action == "none"
    assert state.ensure_target("demo").consecutive_failures == 0


def test_apply_recovery_limited_mode_updates_raw_state() -> None:
    state = GlobalState()
    outcome = recovery.apply_recovery(
        target=make_target(
            services=["demo.service"],
            service_active=True,
            restart_threshold=2,
            reboot_threshold=3,
        ),
        check_result=CheckResult(
            target="demo",
            healthy=False,
            failures=[CheckFailure("service_active", "down")],
            observations={"policy_status": "degraded"},
        ),
        global_config=_global(),
        state=state,
        dry_run=True,
        allow_disruptive_actions=False,
        now_ts=100.0,
    )
    assert outcome.action == "warn"
    ts = state.ensure_target("demo")
    assert ts.last_action == "warn"
    assert ts.last_failure_ts == 100.0


def test_apply_recovery_injected_now_ts_used_for_last_healthy() -> None:
    state = GlobalState()
    outcome = recovery.apply_recovery(
        target=make_target(
            services=["demo.service"],
            service_active=True,
            restart_threshold=2,
            reboot_threshold=3,
        ),
        check_result=CheckResult(target="demo", healthy=True, failures=[]),
        global_config=_global(),
        state=state,
        dry_run=True,
        now_ts=42.0,
    )
    assert outcome.action == "none"
    assert state.ensure_target("demo").last_healthy_ts == 42.0


def test_apply_recovery_restart_cooldown_suppresses_repeat(monkeypatch: Any) -> None:
    state = GlobalState.from_dict(
        {
            "targets": {
                "demo": {
                    "consecutive_failures": 1,
                    "last_action": "restart",
                    "last_action_ts": 95.0,
                }
            }
        }
    )
    outcome = recovery.apply_recovery(
        target=make_target(
            services=["demo.service"],
            service_active=True,
            restart_threshold=2,
            reboot_threshold=9,
        ),
        check_result=CheckResult(
            target="demo",
            healthy=False,
            failures=[CheckFailure("service_active", "down")],
        ),
        global_config=_global(restart_cooldown_sec=10),
        state=state,
        dry_run=True,
        now_ts=100.0,
    )
    assert outcome.action == "warn"
    assert not outcome.requested_reboot


def test_apply_recovery_reboot_is_deferred_and_not_executed(monkeypatch: Any) -> None:
    monkeypatch.setattr(recovery, "read_uptime_sec", lambda: 1000.0)
    reboot_called = {"value": False}

    def fake_trigger_reboot(dry_run: bool, reason: str) -> bool:
        reboot_called["value"] = True
        return True

    monkeypatch.setattr(recovery, "_trigger_reboot", fake_trigger_reboot)
    monkeypatch.setattr(recovery, "_restart_services", lambda services, dry_run: True)
    state = GlobalState()
    outcome = recovery.apply_recovery(
        target=make_target(restart_threshold=1, reboot_threshold=1),
        check_result=CheckResult(
            target="demo",
            healthy=False,
            failures=[CheckFailure("service_active", "down")],
            observations={"policy_status": "failed", "policy_reason": "process_error"},
        ),
        global_config=_global(
            min_uptime_for_reboot_sec=0, restart_cooldown_sec=0, reboot_cooldown_sec=0
        ),
        state=state,
        dry_run=True,
    )
    assert outcome.action == "reboot"
    assert outcome.requested_reboot
    assert reboot_called["value"] is False
    assert state.reboots


def test_apply_recovery_clock_only_blocks_reboot_without_ready() -> None:
    state = GlobalState()
    outcome = recovery.apply_recovery(
        target=make_target(
            services=["demo.service"],
            restart_threshold=1,
            reboot_threshold=1,
        ),
        check_result=CheckResult(
            target="demo",
            healthy=False,
            failures=[CheckFailure("semantic_clock_skew", "clock skew")],
            observations={"clock_reboot_ready": False},
        ),
        global_config=_global(
            min_uptime_for_reboot_sec=0, reboot_cooldown_sec=0, restart_cooldown_sec=0
        ),
        state=state,
        dry_run=True,
    )
    assert outcome.action == "restart"
    assert not outcome.requested_reboot


def test_apply_recovery_clock_only_allows_reboot_when_ready(monkeypatch: Any) -> None:
    monkeypatch.setattr(recovery, "read_uptime_sec", lambda: 1000.0)
    state = GlobalState()
    outcome = recovery.apply_recovery(
        target=make_target(restart_threshold=1, reboot_threshold=1),
        check_result=CheckResult(
            target="demo",
            healthy=False,
            failures=[CheckFailure("semantic_clock_frozen", "clock frozen")],
            observations={"clock_reboot_ready": True, "policy_status": "failed"},
        ),
        global_config=_global(
            min_uptime_for_reboot_sec=0, reboot_cooldown_sec=0, restart_cooldown_sec=0
        ),
        state=state,
        dry_run=True,
    )
    assert outcome.action == "reboot"
    assert outcome.requested_reboot


def test_apply_recovery_reboots_on_confirmed_clock_without_failures(
    monkeypatch: Any,
) -> None:
    monkeypatch.setattr(recovery, "read_uptime_sec", lambda: 1000.0)
    state = GlobalState()
    outcome = recovery.apply_recovery(
        target=make_target(restart_threshold=9, reboot_threshold=9),
        check_result=CheckResult(
            target="demo",
            healthy=True,
            failures=[],
            observations={
                "clock_frozen_confirmed": True,
                "policy_status": "failed",
                "policy_reason": "clock_frozen_confirmed",
            },
        ),
        global_config=_global(
            min_uptime_for_reboot_sec=0, reboot_cooldown_sec=0, restart_cooldown_sec=0
        ),
        state=state,
        dry_run=True,
    )
    assert outcome.action == "reboot"
    assert outcome.requested_reboot


def test_apply_recovery_confirmed_clock_requires_failed_policy(monkeypatch: Any) -> None:
    monkeypatch.setattr(recovery, "read_uptime_sec", lambda: 1000.0)
    state = GlobalState()
    outcome = recovery.apply_recovery(
        target=make_target(restart_threshold=9, reboot_threshold=9),
        check_result=CheckResult(
            target="demo",
            healthy=False,
            failures=[],
            observations={
                "clock_frozen_confirmed": True,
                "policy_status": "degraded",
            },
        ),
        global_config=_global(
            min_uptime_for_reboot_sec=0, reboot_cooldown_sec=0, restart_cooldown_sec=0
        ),
        state=state,
        dry_run=True,
    )
    assert outcome.action == "warn"
    assert not outcome.requested_reboot


def test_apply_recovery_confirmed_clock_blocked_by_reboot_guard(monkeypatch: Any) -> None:
    monkeypatch.setattr(recovery, "read_uptime_sec", lambda: 0.0)
    state = GlobalState()
    outcome = recovery.apply_recovery(
        target=make_target(restart_threshold=9, reboot_threshold=9),
        check_result=CheckResult(
            target="demo",
            healthy=False,
            failures=[CheckFailure("semantic_clock_frozen", "clock")],
            observations={
                "clock_frozen_confirmed": True,
                "policy_status": "failed",
            },
        ),
        global_config=_global(min_uptime_for_reboot_sec=60),
        state=state,
        dry_run=True,
    )
    assert outcome.action == "warn"
    assert not outcome.requested_reboot
