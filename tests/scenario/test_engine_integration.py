"""Integration tests for the engine module orchestration flow."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from conftest import make_app_config, make_target

from raspi_sentinel.checks import CheckResult
from raspi_sentinel.engine import (
    ProcessTargetResult,
    TargetEvaluationArtifacts,
    _evaluate_targets_phase,
    _overall_status,
    _record_state_load_issue_event,
    _run_cycle_collect_locked,
    apply_recovery_phase,
    evaluate_target,
    persist_cycle_outputs,
)
from raspi_sentinel.exit_codes import REBOOT_REQUESTED, UNHEALTHY
from raspi_sentinel.state import StateLoadDiagnostics, TieredStateStore
from raspi_sentinel.state_models import GlobalState


def test_evaluate_target_runs_checks_and_returns_policy(monkeypatch: Any) -> None:
    target = make_target(
        name="svc",
        command="true",
        command_timeout_sec=1,
    )
    state = GlobalState()
    monkeypatch.setattr(
        "raspi_sentinel.engine.run_checks",
        lambda t, now_wall_ts=None: CheckResult(target=t.name, healthy=True, failures=[]),
    )
    pair = evaluate_target(target, state, now_ts=1000.0)
    assert pair is not None
    result, policy = pair
    assert result.healthy
    assert policy.is_ok


def test_evaluate_target_returns_none_when_maintenance_suppressed(
    monkeypatch: Any,
) -> None:
    target = make_target(
        name="svc",
        maintenance_mode_command="echo yes",
        maintenance_mode_timeout_sec=5,
    )
    state = GlobalState()
    monkeypatch.setattr(
        "raspi_sentinel.engine.is_target_suppressed_by_maintenance",
        lambda target, target_state, now_ts: (True, "maintenance on"),
    )
    assert evaluate_target(target, state, now_ts=1000.0) is None


def test_apply_recovery_phase_delegates_to_recovery() -> None:
    config = make_app_config()
    state = GlobalState()
    result = CheckResult(
        target="demo",
        healthy=True,
        failures=[],
    )
    outcome = apply_recovery_phase(
        target=config.targets[0],
        result=result,
        config=config,
        state=state,
        dry_run=True,
        now_ts=1000.0,
    )
    assert outcome.action == "none"
    assert not outcome.requested_reboot


def test_persist_cycle_outputs_saves_state(tmp_path: Path) -> None:
    state_file = tmp_path / "state.json"
    store = TieredStateStore(state_file)
    state = GlobalState()
    state.ensure_target("svc").consecutive_failures = 3
    ok = persist_cycle_outputs(
        store=store,
        state=state,
        max_file_bytes=1_000_000,
        max_reboots_entries=256,
    )
    assert ok
    saved = json.loads(state_file.read_text())
    assert saved["targets"]["svc"]["consecutive_failures"] == 3


def test_overall_status_logic() -> None:
    assert _overall_status({}) == "ok"
    assert _overall_status({"a": {"status": "ok"}}) == "ok"
    assert _overall_status({"a": {"status": "degraded"}}) == "degraded"
    assert _overall_status({"a": {"status": "degraded"}, "b": {"status": "failed"}}) == "failed"


def test_run_cycle_executes_reboot_after_persist(tmp_path: Path, monkeypatch: Any) -> None:
    cfg = make_app_config()
    store = TieredStateStore(tmp_path / "state.json")
    calls: list[str] = []

    monkeypatch.setattr(
        "raspi_sentinel.engine._evaluate_targets_phase",
        lambda **kwargs: TargetEvaluationArtifacts(
            target_results={},
            target_reports={
                "demo": {
                    "status": "failed",
                    "reason": "dependency_gateway",
                    "action": "reboot",
                    "healthy": False,
                    "evidence": {},
                }
            },
            unhealthy_count=1,
            reboot_requested=True,
            reboot_reason="gateway failed",
        ),
    )
    monkeypatch.setattr(
        "raspi_sentinel.engine._run_notification_phase", lambda **kwargs: calls.append("notify")
    )
    monkeypatch.setattr(
        "raspi_sentinel.engine.maybe_write_monitor_stats",
        lambda **kwargs: calls.append("monitor"),
    )
    monkeypatch.setattr(
        "raspi_sentinel.engine.persist_cycle_outputs",
        lambda **kwargs: calls.append("persist") or True,
    )
    monkeypatch.setattr(
        "raspi_sentinel.engine.execute_deferred_reboot",
        lambda **kwargs: calls.append("reboot") or True,
    )

    rc, report = _run_cycle_collect_locked(
        config=cfg,
        dry_run=False,
        store=store,
        now_ts=1000.0,
        mono_provider=lambda: 1.0,
    )

    assert rc == REBOOT_REQUESTED
    assert report["reboot_requested"] is True
    assert calls.index("persist") < calls.index("reboot")


def test_run_cycle_reboot_command_failure_returns_unhealthy(
    tmp_path: Path, monkeypatch: Any
) -> None:
    cfg = make_app_config()
    store = TieredStateStore(tmp_path / "state.json")

    monkeypatch.setattr(
        "raspi_sentinel.engine._evaluate_targets_phase",
        lambda **kwargs: TargetEvaluationArtifacts(
            target_results={},
            target_reports={
                "demo": {
                    "status": "failed",
                    "reason": "dependency_gateway",
                    "action": "reboot",
                    "healthy": False,
                    "evidence": {},
                }
            },
            unhealthy_count=1,
            reboot_requested=True,
            reboot_reason="gateway failed",
        ),
    )
    monkeypatch.setattr("raspi_sentinel.engine._run_notification_phase", lambda **kwargs: None)
    monkeypatch.setattr("raspi_sentinel.engine.maybe_write_monitor_stats", lambda **kwargs: None)
    monkeypatch.setattr("raspi_sentinel.engine.persist_cycle_outputs", lambda **kwargs: True)
    monkeypatch.setattr("raspi_sentinel.engine.execute_deferred_reboot", lambda **kwargs: False)

    rc, report = _run_cycle_collect_locked(
        config=cfg,
        dry_run=False,
        store=store,
        now_ts=1000.0,
        mono_provider=lambda: 1.0,
    )

    assert rc == UNHEALTHY
    assert report["reason"] == "reboot_command_failed"


def test_run_cycle_sets_reason_on_state_persist_failure_after_reboot_intent(
    tmp_path: Path, monkeypatch: Any
) -> None:
    cfg = make_app_config()
    store = TieredStateStore(tmp_path / "state.json")

    monkeypatch.setattr(
        "raspi_sentinel.engine._evaluate_targets_phase",
        lambda **kwargs: TargetEvaluationArtifacts(
            target_results={},
            target_reports={
                "demo": {
                    "status": "failed",
                    "reason": "process_error",
                    "action": "reboot",
                    "healthy": False,
                    "evidence": {},
                }
            },
            unhealthy_count=1,
            reboot_requested=True,
            reboot_reason="policy failed",
        ),
    )
    monkeypatch.setattr("raspi_sentinel.engine._run_notification_phase", lambda **kwargs: None)
    monkeypatch.setattr("raspi_sentinel.engine.maybe_write_monitor_stats", lambda **kwargs: None)
    monkeypatch.setattr("raspi_sentinel.engine.persist_cycle_outputs", lambda **kwargs: False)

    rc, report = _run_cycle_collect_locked(
        config=cfg,
        dry_run=False,
        store=store,
        now_ts=1000.0,
        mono_provider=lambda: 1.0,
    )
    assert rc != REBOOT_REQUESTED
    assert report["state_persisted"] is False
    assert report["reason"] == "state_persist_failed_after_reboot_intent"


def test_evaluate_targets_phase_stops_after_first_reboot_request(monkeypatch: Any) -> None:
    cfg = make_app_config(
        targets=[make_target(name="a"), make_target(name="b"), make_target(name="c")]
    )
    calls: list[str] = []

    def fake_process_single_target(**kwargs: Any) -> ProcessTargetResult:
        target = kwargs["target"]
        calls.append(target.name)
        if target.name == "b":
            return ProcessTargetResult(
                report={"status": "failed", "reason": "process_error", "action": "reboot"},
                result=CheckResult(target=target.name, healthy=False, failures=[]),
                policy_status="failed",
                reboot_requested=True,
                reboot_reason="failed",
            )
        return ProcessTargetResult(
            report={"status": "ok", "reason": "healthy", "action": "none"},
            result=CheckResult(target=target.name, healthy=True, failures=[]),
            policy_status="ok",
            reboot_requested=False,
            reboot_reason=None,
        )

    monkeypatch.setattr("raspi_sentinel.engine._process_single_target", fake_process_single_target)

    artifacts = _evaluate_targets_phase(
        config=cfg,
        state=GlobalState(),
        dry_run=True,
        now_ts=1000.0,
        mono_provider=lambda: 1.0,
        limited_mode=False,
        notifier=type("DummyNotifier", (), {"enabled": False})(),
        events_file=Path("/tmp/events.jsonl"),
        events_max=0,
        events_backups=1,
        notifications_enabled=False,
    )
    assert calls == ["a", "b"]
    assert artifacts.reboot_requested is True
    assert artifacts.reboot_reason == "failed"
    assert "c" not in artifacts.target_reports


def test_run_cycle_suppresses_notifications_in_dry_run_by_default(
    tmp_path: Path, monkeypatch: Any
) -> None:
    cfg = make_app_config(
        discord_overrides={
            "enabled": True,
            "webhook_url": "https://discord.com/api/webhooks/123/abc",
            "heartbeat_interval_sec": 0,
        },
        targets=[make_target(name="demo", command="false", command_timeout_sec=1)],
    )
    store = TieredStateStore(tmp_path / "state.json")
    calls: list[str] = []

    monkeypatch.setattr(
        "raspi_sentinel.notify.DiscordNotifier.send_lines",
        lambda *a, **k: calls.append("sent") or True,
    )
    rc, report = _run_cycle_collect_locked(
        config=cfg,
        dry_run=True,
        store=store,
        now_ts=1000.0,
        mono_provider=lambda: 1.0,
    )
    assert rc == UNHEALTHY
    assert report["dry_run"] is True
    assert calls == []


def test_run_cycle_allows_notifications_in_dry_run_when_opted_in(
    tmp_path: Path, monkeypatch: Any
) -> None:
    cfg = make_app_config(
        discord_overrides={
            "enabled": True,
            "webhook_url": "https://discord.com/api/webhooks/123/abc",
            "heartbeat_interval_sec": 0,
        },
        targets=[make_target(name="demo", command="false", command_timeout_sec=1)],
    )
    store = TieredStateStore(tmp_path / "state.json")
    calls: list[str] = []

    monkeypatch.setattr(
        "raspi_sentinel.notify.DiscordNotifier.send_lines",
        lambda *a, **k: calls.append("sent") or True,
    )
    rc, report = _run_cycle_collect_locked(
        config=cfg,
        dry_run=True,
        store=store,
        now_ts=1000.0,
        mono_provider=lambda: 1.0,
        send_notifications_in_dry_run=True,
    )
    assert rc == UNHEALTHY
    assert report["dry_run"] is True
    assert calls


def test_record_state_load_issue_event_omits_reason_when_no_detail(tmp_path: Path) -> None:
    events_file = tmp_path / "events.jsonl"
    diagnostics = StateLoadDiagnostics(
        state_corrupted=True,
        state_load_error=None,
    )
    _record_state_load_issue_event(
        diagnostics=diagnostics,
        events_file=events_file,
        max_file_bytes=1_000_000,
        backup_generations=1,
        now_ts=1_000.0,
    )
    event = json.loads(events_file.read_text(encoding="utf-8").strip())
    assert event["kind"] == "state_corrupted"
    assert "reason" not in event
