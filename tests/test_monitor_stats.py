from __future__ import annotations

from pathlib import Path

from raspi_sentinel.checks import CheckResult
from raspi_sentinel.config import (
    AppConfig,
    DiscordNotifyConfig,
    GlobalConfig,
    NotifyConfig,
    TargetConfig,
)
from raspi_sentinel.monitor_stats import build_monitor_stats_snapshot
from raspi_sentinel.state_models import GlobalState


def _global() -> GlobalConfig:
    return GlobalConfig(
        state_file=Path("/tmp/state.json"),
        state_max_file_bytes=2_000_000,
        state_reboots_max_entries=256,
        state_lock_timeout_sec=5,
        events_file=Path("/tmp/events.jsonl"),
        events_max_file_bytes=5_000_000,
        events_backup_generations=3,
        monitor_stats_file=Path("/tmp/stats.json"),
        monitor_stats_interval_sec=30,
        restart_threshold=2,
        reboot_threshold=3,
        restart_cooldown_sec=10,
        reboot_cooldown_sec=20,
        reboot_window_sec=300,
        max_reboots_in_window=2,
        min_uptime_for_reboot_sec=60,
        default_command_timeout_sec=5,
        loop_interval_sec=30,
    )


def _target() -> TargetConfig:
    return TargetConfig(
        name="network_uplink",
        services=[],
        service_active=False,
        heartbeat_file=None,
        heartbeat_max_age_sec=None,
        output_file=None,
        output_max_age_sec=None,
        command=None,
        command_use_shell=False,
        command_timeout_sec=None,
        dns_check_command=None,
        dns_check_use_shell=False,
        dns_server_check_command=None,
        dns_server_check_use_shell=False,
        gateway_check_command=None,
        gateway_check_use_shell=False,
        link_check_command=None,
        link_check_use_shell=False,
        default_route_check_command=None,
        default_route_check_use_shell=False,
        internet_ip_check_command=None,
        internet_ip_check_use_shell=False,
        wan_vs_target_check_command=None,
        wan_vs_target_check_use_shell=False,
        network_probe_enabled=True,
        network_interface="wlan0",
        gateway_probe_timeout_sec=2,
        internet_ip_targets=["1.1.1.1", "8.8.8.8"],
        dns_query_target="example.com",
        http_probe_target="https://example.com",
        consecutive_failure_thresholds={"degraded": 2, "failed": 6},
        latency_thresholds_ms={},
        packet_loss_thresholds_pct={},
        dependency_check_timeout_sec=None,
        stats_file=None,
        stats_updated_max_age_sec=None,
        stats_last_input_max_age_sec=None,
        stats_last_success_max_age_sec=None,
        stats_records_stall_cycles=None,
        time_health_enabled=False,
        check_interval_threshold_sec=30,
        wall_clock_freeze_min_monotonic_sec=25,
        wall_clock_freeze_max_wall_advance_sec=1,
        wall_clock_drift_threshold_sec=30,
        http_time_probe_url=None,
        http_time_probe_timeout_sec=5,
        clock_skew_threshold_sec=300,
        clock_anomaly_reboot_consecutive=3,
        maintenance_mode_command=None,
        maintenance_mode_use_shell=False,
        maintenance_mode_timeout_sec=None,
        maintenance_grace_sec=None,
        restart_threshold=None,
        reboot_threshold=None,
    )


def test_monitor_stats_preserves_unknown_vs_false_for_network_layers() -> None:
    config = AppConfig(
        global_config=_global(),
        notify_config=NotifyConfig(
            discord=DiscordNotifyConfig(
                enabled=False,
                webhook_url=None,
                username="raspi-sentinel",
                timeout_sec=5,
                followup_delay_sec=300,
                heartbeat_interval_sec=0,
            )
        ),
        targets=[_target()],
    )
    state = GlobalState()
    result = CheckResult(
        target="network_uplink",
        healthy=False,
        failures=[],
        observations={
            "network_probe_enabled": True,
            "link_ok": None,
            "iface_up": None,
            "wifi_associated": None,
            "ip_assigned": True,
            "gateway_ok": False,
            "neighbor_resolved": False,
            "arp_gateway_ok": None,
            "default_route_iface": "wlan0",
            "gateway_ip": "192.168.1.1",
        },
    )

    snapshot = build_monitor_stats_snapshot(
        config=config,
        state=state,
        target_results={"network_uplink": result},
        now_ts=1_000_000.0,
    )
    payload = snapshot["targets"]["network_uplink"]
    assert payload["link_ok"] is None
    assert payload["iface_up"] is None
    assert payload["wifi_associated"] is None
    assert payload["ip_assigned"] is True
    assert payload["gateway_ok"] is False
    assert payload["neighbor_resolved"] is False
    assert payload["arp_gateway_ok"] is None
