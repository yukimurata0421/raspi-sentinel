from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from raspi_sentinel import cli, config_summary
from raspi_sentinel.checks import CheckFailure, CheckResult
from raspi_sentinel.config import load_config
from raspi_sentinel.status_events import classify_target_reason, classify_target_status


def _write(path: Path, text: str) -> None:
    path.write_text(text.strip() + "\n", encoding="utf-8")


def test_classify_status_prioritizes_dependency_types() -> None:
    assert (
        classify_target_status(CheckResult("t", False, [CheckFailure("dependency_dns", "x")]))
        == "degraded"
    )
    assert (
        classify_target_reason(CheckResult("t", False, [CheckFailure("dependency_dns", "x")]))
        == "dns_error"
    )
    assert (
        classify_target_status(CheckResult("t", False, [CheckFailure("dependency_gateway", "x")]))
        == "degraded"
    )
    assert (
        classify_target_reason(CheckResult("t", False, [CheckFailure("dependency_gateway", "x")]))
        == "gateway_error"
    )
    assert (
        classify_target_status(
            CheckResult("t", False, [CheckFailure("semantic_last_success_ts", "x")])
        )
        == "degraded"
    )


def test_run_cycle_writes_monitor_stats_and_events(tmp_path: Path, monkeypatch: Any) -> None:
    conf = tmp_path / "config.toml"
    state_file = tmp_path / "state.json"
    events_file = tmp_path / "events.jsonl"
    monitor_stats_file = tmp_path / "stats.json"
    _write(
        conf,
        f"""
        [global]
        state_file = "{state_file}"
        events_file = "{events_file}"
        monitor_stats_file = "{monitor_stats_file}"
        monitor_stats_interval_sec = 30
        restart_threshold = 2
        reboot_threshold = 4
        restart_cooldown_sec = 10
        reboot_cooldown_sec = 10
        reboot_window_sec = 300
        max_reboots_in_window = 2
        min_uptime_for_reboot_sec = 0
        default_command_timeout_sec = 2
        loop_interval_sec = 30

        [notify.discord]
        enabled = false
        username = "raspi-sentinel"
        timeout_sec = 5
        followup_delay_sec = 300
        heartbeat_interval_sec = 0

        [[targets]]
        name = "self_test"
        services = []
        service_active = false
        command = "true"
        command_timeout_sec = 2
        """,
    )
    config = load_config(conf)

    monkeypatch.setattr(cli.time, "time", lambda: 1000.0)
    rc1 = cli._run_cycle(config=config, dry_run=True)
    assert rc1 == 0
    content1 = monitor_stats_file.read_text(encoding="utf-8")
    data1 = json.loads(content1)
    assert data1["service"] == "raspi-sentinel"
    assert data1["targets_total"] == 1
    assert data1["targets_ok"] == 1
    assert data1["targets_degraded"] == 0
    assert data1["targets_failed"] == 0
    events_text = events_file.read_text(encoding="utf-8")
    assert '"reason": "healthy"' in events_text
    assert '"from": "unknown"' in events_text
    assert '"to": "ok"' in events_text

    monkeypatch.setattr(cli.time, "time", lambda: 1010.0)
    rc2 = cli._run_cycle(config=config, dry_run=True)
    assert rc2 == 0
    content2 = monitor_stats_file.read_text(encoding="utf-8")
    assert content2 == content1


def test_run_once_json_outputs_machine_readable_cycle_summary(
    tmp_path: Path, monkeypatch: Any, capsys: Any
) -> None:
    conf = tmp_path / "config.toml"
    state_file = tmp_path / "state.json"
    events_file = tmp_path / "events.jsonl"
    monitor_stats_file = tmp_path / "stats.json"
    _write(
        conf,
        f"""
        [global]
        state_file = "{state_file}"
        events_file = "{events_file}"
        monitor_stats_file = "{monitor_stats_file}"
        monitor_stats_interval_sec = 30
        restart_threshold = 2
        reboot_threshold = 4
        restart_cooldown_sec = 10
        reboot_cooldown_sec = 10
        reboot_window_sec = 300
        max_reboots_in_window = 2
        min_uptime_for_reboot_sec = 0
        default_command_timeout_sec = 2
        loop_interval_sec = 30

        [notify.discord]
        enabled = false
        username = "raspi-sentinel"
        timeout_sec = 5
        followup_delay_sec = 300
        heartbeat_interval_sec = 0

        [[targets]]
        name = "self_test"
        services = []
        service_active = false
        command = "true"
        command_timeout_sec = 2
        """,
    )
    monkeypatch.setattr(cli.time, "time", lambda: 1_000.0)

    rc = cli.main(["-c", str(conf), "--dry-run", "run-once", "--json"])
    assert rc == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["overall_status"] == "ok"
    assert payload["dry_run"] is True
    assert payload["reboot_requested"] is False
    assert payload["targets"]["self_test"]["status"] == "ok"
    assert payload["targets"]["self_test"]["reason"] == "healthy"
    assert payload["targets"]["self_test"]["action"] == "none"


def test_validate_config_json_includes_rules_warnings_and_shell_targets(
    tmp_path: Path, monkeypatch: Any, capsys: Any
) -> None:
    conf = tmp_path / "config.toml"
    heartbeat_missing = tmp_path / "missing-heartbeat.txt"
    _write(
        conf,
        f"""
        [global]
        state_file = "/tmp/state.json"
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
        service_active = false
        command = "true"
        heartbeat_file = "{heartbeat_missing}"
        heartbeat_max_age_sec = 60
        """,
    )
    monkeypatch.setattr(
        config_summary,
        "_check_service_unit_load_state",
        lambda unit, timeout_sec=3: "loaded",
    )

    rc = cli.main(["-c", str(conf), "validate-config", "--json"])
    assert rc == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["config_path"] == str(conf)
    assert payload["shell_command_targets"] == ["demo"]
    target = payload["targets"][0]
    assert target["name"] == "demo"
    assert "command" in target["enabled_rules"]
    assert target["shell_commands"]["command"] == "true"
    assert any("heartbeat_file path does not exist now" in msg for msg in target["warnings"])


def test_validate_config_returns_error_code_for_invalid_config(tmp_path: Path) -> None:
    conf = tmp_path / "invalid.toml"
    _write(
        conf,
        """
        [global]
        state_file = "/tmp/state.json"

        [notify.discord]
        enabled = false
        username = "raspi-sentinel"
        timeout_sec = 5
        followup_delay_sec = 300
        heartbeat_interval_sec = 0
        """,
    )

    rc = cli.main(["-c", str(conf), "validate-config"])
    assert rc == 10
