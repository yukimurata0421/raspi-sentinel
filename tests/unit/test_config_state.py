from __future__ import annotations

from pathlib import Path

import pytest

from raspi_sentinel.config import load_config
from raspi_sentinel.state import StateStore, TieredStateStore
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
    assert cfg.global_config.state_durable_file is None
    assert cfg.global_config.state_durable_fields == ()


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


def test_load_config_accepts_network_probe_settings(tmp_path: Path) -> None:
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
        network_probe_enabled = true
        network_interface = "wlan0"
        gateway_probe_timeout_sec = 2
        internet_ip_targets = ["1.1.1.1", "8.8.8.8"]
        dns_query_target = "example.com"
        http_probe_target = "https://www.google.com/generate_204"

        [targets.consecutive_failure_thresholds]
        degraded = 2
        failed = 6

        [targets.latency_thresholds_ms]
        gateway = 100
        internet_ip = 300
        dns = 400
        http_total = 1200

        [targets.packet_loss_thresholds_pct]
        gateway = 20
        internet_ip = 25
        """,
    )
    cfg = load_config(conf)
    target = cfg.targets[0]
    assert target.network_probe_enabled is True
    assert target.network_interface == "wlan0"
    assert target.internet_ip_targets == ["1.1.1.1", "8.8.8.8"]
    assert target.consecutive_failure_thresholds["degraded"] == 2
    assert target.latency_thresholds_ms["gateway"] == 100.0


def test_load_config_storage_section_overrides_paths_and_fields(tmp_path: Path) -> None:
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

        [storage]
        snapshot_path = "/run/raspi-sentinel/stats.json"
        state_volatile_path = "/run/raspi-sentinel/state.volatile.json"
        state_durable_path = "/var/lib/raspi-sentinel/state.durable.json"
        events_path = "/var/lib/raspi-sentinel/events.jsonl"
        state_durable_fields = ["reboot_history", "followup_schedule", "notify_backlog"]
        require_tmpfs = true
        verify_min_free_bytes = 2048
        verify_write_bytes = 64
        verify_cooldown_sec = 1

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
        command = "true"
        """,
    )
    cfg = load_config(conf)
    assert cfg.global_config.state_file == Path("/run/raspi-sentinel/state.volatile.json")
    assert cfg.global_config.state_durable_file == Path(
        "/var/lib/raspi-sentinel/state.durable.json"
    )
    assert cfg.global_config.monitor_stats_file == Path("/run/raspi-sentinel/stats.json")
    assert cfg.global_config.state_durable_fields == (
        "reboot_history",
        "followup_schedule",
        "notify_backlog",
    )
    assert cfg.global_config.storage_require_tmpfs is True
    assert cfg.global_config.storage_verify_min_free_bytes == 2048
    assert cfg.global_config.storage_verify_write_bytes == 64
    assert cfg.global_config.storage_verify_cooldown_sec == 1


def test_load_config_storage_require_tmpfs_defaults_to_false(tmp_path: Path) -> None:
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

        [storage]
        state_volatile_path = "/run/raspi-sentinel/state.volatile.json"
        state_durable_path = "/var/lib/raspi-sentinel/state.durable.json"
        state_durable_fields = ["reboot_history"]

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
        command = "true"
        """,
    )
    cfg = load_config(conf)
    assert cfg.global_config.storage_require_tmpfs is False


def test_load_config_rejects_unknown_storage_durable_field(tmp_path: Path) -> None:
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

        [storage]
        state_volatile_path = "/run/raspi-sentinel/state.volatile.json"
        state_durable_path = "/var/lib/raspi-sentinel/state.durable.json"
        state_durable_fields = ["unknown_field"]

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
        command = "true"
        """,
    )
    with pytest.raises(ValueError, match="state_durable_fields contains unknown value"):
        load_config(conf)


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


def test_state_store_quarantines_corrupted_json(tmp_path: Path) -> None:
    state_file = tmp_path / "state.json"
    state_file.write_text("{broken", encoding="utf-8")
    store = StateStore(state_file)

    loaded, diagnostics = store.load_with_diagnostics()
    assert diagnostics.state_corrupted is True
    assert diagnostics.used_default_state is True
    assert diagnostics.corrupt_backup_path is not None
    assert diagnostics.corrupt_backup_path.exists()
    assert not state_file.exists()
    assert loaded["targets"] == {}


def test_tiered_state_store_splits_durable_fields(tmp_path: Path) -> None:
    volatile = tmp_path / "state.volatile.json"
    durable = tmp_path / "state.durable.json"
    store = TieredStateStore(
        volatile_path=volatile,
        durable_path=durable,
        durable_fields=("reboot_history", "followup_schedule", "notify_backlog"),
    )
    payload = {
        "targets": {"demo": {"consecutive_failures": 2, "last_reason": "clock_jump"}},
        "reboots": [{"ts": 10.0, "target": "demo", "reason": "failed"}],
        "followups": {
            "demo": {
                "due_ts": 20.0,
                "created_ts": 10.0,
                "initial_action": "restart",
                "initial_reason": "failed",
                "initial_consecutive_failures": 2,
            }
        },
        "notify": {
            "last_heartbeat_ts": 11.0,
            "retry_due_ts": 30.0,
            "delivery_backlog": {
                "first_failed_ts": 12.0,
                "last_failed_ts": 13.0,
                "total_failures": 2,
                "contexts": {"issue_notification:demo": 2},
            },
        },
        "monitor_stats": {"last_written_ts": 9.0},
    }
    assert store.save(payload, max_file_bytes=1_000_000, max_reboots_entries=256)

    volatile_raw = volatile.read_text(encoding="utf-8")
    assert '"reboots"' not in volatile_raw
    assert '"followups"' not in volatile_raw
    assert '"delivery_backlog"' not in volatile_raw
    assert '"retry_due_ts"' not in volatile_raw

    durable_raw = durable.read_text(encoding="utf-8")
    assert '"reboots"' in durable_raw
    assert '"followups"' in durable_raw
    assert '"delivery_backlog"' in durable_raw
    assert '"retry_due_ts"' in durable_raw

    loaded = store.load()
    assert loaded["targets"]["demo"]["consecutive_failures"] == 2
    assert loaded["reboots"][0]["target"] == "demo"
    assert loaded["followups"]["demo"]["initial_action"] == "restart"
    assert loaded["notify"]["delivery_backlog"]["total_failures"] == 2


def test_tiered_state_store_keeps_tiered_mode_when_durable_file_is_configured(
    tmp_path: Path,
) -> None:
    volatile = tmp_path / "state.volatile.json"
    durable = tmp_path / "state.durable.json"
    store = TieredStateStore(
        volatile_path=volatile,
        durable_path=durable,
        durable_fields=(),
    )
    payload = {
        "targets": {"demo": {"consecutive_failures": 2}},
        "reboots": [{"ts": 10.0, "target": "demo", "reason": "failed"}],
        "followups": {},
        "notify": {},
        "monitor_stats": {},
    }

    assert store.save(payload, max_file_bytes=1_000_000, max_reboots_entries=256)
    assert volatile.exists()
    assert durable.exists()
    assert durable.read_text(encoding="utf-8").strip() == "{}"

    loaded = store.load()
    assert loaded["targets"]["demo"]["consecutive_failures"] == 2
    assert loaded["reboots"][0]["target"] == "demo"


def test_tiered_state_store_returns_false_when_durable_save_fails(tmp_path: Path) -> None:
    volatile = tmp_path / "state.volatile.json"
    durable = tmp_path / "state.durable.json"
    store = TieredStateStore(
        volatile_path=volatile,
        durable_path=durable,
        durable_fields=("reboot_history", "followup_schedule", "notify_backlog"),
    )
    payload = {
        "targets": {"demo": {"consecutive_failures": 1}},
        "reboots": [{"ts": 10.0, "target": "demo", "reason": "failed"}],
        "followups": {},
        "notify": {},
        "monitor_stats": {},
    }
    calls: list[Path] = []
    original = store._save_raw_payload

    def fake_save_raw_payload(
        *, path: Path, payload: dict[str, object], max_file_bytes: int
    ) -> bool:
        calls.append(path)
        if path == durable:
            return False
        return original(path=path, payload=payload, max_file_bytes=max_file_bytes)

    store._save_raw_payload = fake_save_raw_payload  # type: ignore[method-assign]
    ok = store.save(payload, max_file_bytes=1_000_000, max_reboots_entries=256)
    assert ok is False
    assert calls == [volatile, durable]
    assert volatile.exists()


def test_tiered_state_store_initializes_missing_parent_directories(tmp_path: Path) -> None:
    volatile = tmp_path / "run" / "raspi-sentinel" / "state.volatile.json"
    durable = tmp_path / "var" / "lib" / "raspi-sentinel" / "state.durable.json"
    assert not volatile.parent.exists()
    assert not durable.parent.exists()

    TieredStateStore(
        volatile_path=volatile,
        durable_path=durable,
        durable_fields=("reboot_history",),
    )

    assert volatile.parent.is_dir()
    assert durable.parent.is_dir()
