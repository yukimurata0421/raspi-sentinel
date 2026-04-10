from __future__ import annotations

from pathlib import Path

import pytest

from raspi_sentinel.config import load_config
from raspi_sentinel.state import StateStore
from raspi_sentinel.state_helpers import maybe_rotate_file


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


def test_load_config_sets_dependency_timeout_from_global_default(
    tmp_path: Path,
) -> None:
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


def test_state_store_save_blocks_oversized_payload(tmp_path: Path) -> None:
    state_file = tmp_path / "state.json"
    store = StateStore(state_file)
    payload = {
        "targets": {},
        "reboots": [],
        "followups": {},
        "notify": {"big": "x" * 5000},
        "monitor_stats": {},
    }
    ok = store.save(payload, max_file_bytes=128, max_reboots_entries=256)
    assert not ok
    assert not state_file.exists()


def test_state_store_save_trims_reboots_list(tmp_path: Path) -> None:
    state_file = tmp_path / "state.json"
    store = StateStore(state_file)
    payload = {
        "targets": {},
        "reboots": [{"ts": float(i)} for i in range(10)],
        "followups": {},
        "notify": {},
        "monitor_stats": {},
    }
    ok = store.save(payload, max_file_bytes=10_000, max_reboots_entries=3)
    assert ok
    loaded = store.load()
    assert len(loaded["reboots"]) == 3
    assert loaded["reboots"][0]["ts"] == 7.0


def test_maybe_rotate_file_supports_multiple_generations(tmp_path: Path) -> None:
    events = tmp_path / "events.jsonl"
    events.write_text("current\n", encoding="utf-8")
    (tmp_path / "events.jsonl.1").write_text("old-1\n", encoding="utf-8")
    (tmp_path / "events.jsonl.2").write_text("old-2\n", encoding="utf-8")
    (tmp_path / "events.jsonl.3").write_text("old-3\n", encoding="utf-8")

    maybe_rotate_file(events, max_bytes=1, backup_generations=3)

    assert not events.exists()
    assert (tmp_path / "events.jsonl.1").read_text(encoding="utf-8") == "current\n"
    assert (tmp_path / "events.jsonl.2").read_text(encoding="utf-8") == "old-1\n"
    assert (tmp_path / "events.jsonl.3").read_text(encoding="utf-8") == "old-2\n"
