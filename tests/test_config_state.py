from __future__ import annotations

from pathlib import Path

import pytest

from raspi_sentinel.config import load_config
from raspi_sentinel.state import StateStore


def _write(path: Path, text: str) -> None:
    path.write_text(text.strip() + "\n", encoding="utf-8")


def test_load_config_defaults_include_monitor_stats(tmp_path: Path) -> None:
    conf = tmp_path / "config.toml"
    _write(
        conf,
        """
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
        """,
    )
    cfg = load_config(conf)
    assert cfg.global_config.events_file == Path("/var/lib/raspi-sentinel/events.jsonl")
    assert cfg.global_config.monitor_stats_file == Path("/var/lib/raspi-sentinel/stats.json")
    assert cfg.global_config.monitor_stats_interval_sec == 30


def test_load_config_rejects_stats_rules_without_stats_file(tmp_path: Path) -> None:
    conf = tmp_path / "config.toml"
    _write(
        conf,
        """
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
        stats_updated_max_age_sec = 60
        command = "true"
        """,
    )
    with pytest.raises(ValueError, match="stats_file is required"):
        load_config(conf)


def test_load_config_sets_dependency_timeout_from_global_default(tmp_path: Path) -> None:
    conf = tmp_path / "config.toml"
    _write(
        conf,
        """
        [global]
        state_file = "/tmp/state.json"
        restart_threshold = 2
        reboot_threshold = 3
        restart_cooldown_sec = 10
        reboot_cooldown_sec = 20
        reboot_window_sec = 300
        max_reboots_in_window = 2
        min_uptime_for_reboot_sec = 60
        default_command_timeout_sec = 7
        loop_interval_sec = 30

        [notify.discord]
        enabled = false
        username = "raspi-sentinel"
        timeout_sec = 5
        followup_delay_sec = 300
        heartbeat_interval_sec = 0

        [[targets]]
        name = "network_uplink"
        services = []
        service_active = false
        dns_check_command = "true"
        gateway_check_command = "true"
        """,
    )
    cfg = load_config(conf)
    assert cfg.targets[0].dependency_check_timeout_sec == 7


def test_state_store_round_trip_preserves_monitor_stats(tmp_path: Path) -> None:
    state_file = tmp_path / "state.json"
    store = StateStore(state_file)
    payload = {
        "targets": {"demo": {"consecutive_failures": 1}},
        "reboots": [],
        "followups": {},
        "notify": {},
        "monitor_stats": {"last_written_ts": 123.0},
    }
    store.save(payload)
    loaded = store.load()
    assert loaded["monitor_stats"]["last_written_ts"] == 123.0
