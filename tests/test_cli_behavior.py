from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from raspi_sentinel import cli
from raspi_sentinel.checks import CheckFailure, CheckResult
from raspi_sentinel.config import load_config


def _write(path: Path, text: str) -> None:
    path.write_text(text.strip() + "\n", encoding="utf-8")


def test_classify_status_prioritizes_dependency_types() -> None:
    assert cli._classify_target_status(
        CheckResult("t", False, [CheckFailure("dependency_dns", "x")])
    ) == "degraded"
    assert cli._classify_target_reason(
        CheckResult("t", False, [CheckFailure("dependency_dns", "x")])
    ) == "dns_error"
    assert cli._classify_target_status(
        CheckResult("t", False, [CheckFailure("dependency_gateway", "x")])
    ) == "degraded"
    assert cli._classify_target_reason(
        CheckResult("t", False, [CheckFailure("dependency_gateway", "x")])
    ) == "gateway_error"
    assert cli._classify_target_status(
        CheckResult("t", False, [CheckFailure("semantic_last_success_ts", "x")])
    ) == "degraded"


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
