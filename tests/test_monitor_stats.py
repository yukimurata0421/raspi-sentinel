from __future__ import annotations

from typing import Any

from conftest import make_global_config, make_target

from raspi_sentinel.checks import CheckResult
from raspi_sentinel.config import AppConfig, DiscordNotifyConfig, NotifyConfig, TargetConfig
from raspi_sentinel.monitor_stats import build_monitor_stats_snapshot
from raspi_sentinel.state_models import GlobalState


def _global() -> Any:
    return make_global_config(
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
    return make_target(
        name="network_uplink",
        network_probe_enabled=True,
        network_interface="wlan0",
        dns_query_target="example.com",
        http_probe_target="https://example.com",
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
                notify_on_recovery=False,
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
            "policy_subreason": "all_targets_failed",
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
    assert payload["subreason"] == "all_targets_failed"
    assert payload["link_ok"] is None
    assert payload["iface_up"] is None
    assert payload["wifi_associated"] is None
    assert payload["ip_assigned"] is True
    assert payload["gateway_ok"] is False
    assert payload["neighbor_resolved"] is False
    assert payload["arp_gateway_ok"] is None
