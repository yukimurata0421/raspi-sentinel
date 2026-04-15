from __future__ import annotations

import logging
import stat
from pathlib import Path

import pytest
from conftest import make_target

from raspi_sentinel.config import (
    _optional_bool,
    _optional_float_from_mapping,
    _optional_int,
    _optional_path,
    _optional_str,
    _optional_str_list,
    _require_int,
    _validate_target_rules,
    _warn_config_permissions,
    load_config,
)


def test_require_int_rejects_non_int() -> None:
    with pytest.raises(ValueError, match="'k' must be an integer"):
        _require_int({"k": "1"}, "k")


def test_require_int_rejects_missing_without_default() -> None:
    with pytest.raises(ValueError, match="'missing' must be an integer"):
        _require_int({}, "missing")


def test_optional_int_rejects_non_int_when_set() -> None:
    with pytest.raises(ValueError, match="'n' must be an integer"):
        _optional_int({"n": 1.5}, "n")


def test_optional_str_rejects_empty_or_whitespace() -> None:
    with pytest.raises(ValueError, match="'s' must be a non-empty string"):
        _optional_str({"s": ""}, "s")
    with pytest.raises(ValueError, match="'s' must be a non-empty string"):
        _optional_str({"s": "   "}, "s")
    with pytest.raises(ValueError, match="'s' must be a non-empty string"):
        _optional_str({"s": 1}, "s")


def test_optional_path_rejects_invalid_string() -> None:
    with pytest.raises(ValueError, match="'p' must be a non-empty string"):
        _optional_path({"p": ""}, "p")


def test_optional_bool_rejects_non_bool() -> None:
    with pytest.raises(ValueError, match="'b' must be boolean"):
        _optional_bool({"b": 1}, "b", default=False)


def test_optional_str_list_rejects_invalid_entries() -> None:
    with pytest.raises(ValueError, match="'l' must be an array of non-empty strings"):
        _optional_str_list({"l": "x"}, "l")
    with pytest.raises(ValueError, match="'l' must be an array of non-empty strings"):
        _optional_str_list({"l": ["ok", ""]}, "l")
    with pytest.raises(ValueError, match="'l' must be an array of non-empty strings"):
        _optional_str_list({"l": [1, "a"]}, "l")


def test_optional_float_from_mapping_rejects_bool_and_invalid_types() -> None:
    with pytest.raises(ValueError, match="'f' must be a number"):
        _optional_float_from_mapping({"f": True}, "f")
    with pytest.raises(ValueError, match="'f' must be a number"):
        _optional_float_from_mapping({"f": "nope"}, "f")


def test_target_getattr_unknown_raises() -> None:
    t = make_target(command="true")
    with pytest.raises(AttributeError, match="has no attribute 'no_such_field'"):
        _ = t.no_such_field


def test_validate_heartbeat_file_requires_max_age() -> None:
    with pytest.raises(ValueError, match="heartbeat_file and heartbeat_max_age_sec must be set"):
        _validate_target_rules(
            make_target(
                heartbeat_file=Path("/tmp/hb"),
                heartbeat_max_age_sec=None,
                command="true",
            )
        )


def test_validate_heartbeat_max_age_requires_file() -> None:
    with pytest.raises(ValueError, match="heartbeat_file and heartbeat_max_age_sec must be set"):
        _validate_target_rules(
            make_target(
                heartbeat_max_age_sec=60,
                command="true",
            )
        )


def test_validate_output_pairing_and_positive_age() -> None:
    with pytest.raises(ValueError, match="output_file and output_max_age_sec must be set"):
        _validate_target_rules(
            make_target(
                output_file=Path("/tmp/o"),
                output_max_age_sec=None,
                command="true",
            )
        )
    with pytest.raises(ValueError, match="output_max_age_sec must be > 0"):
        _validate_target_rules(
            make_target(
                output_file=Path("/tmp/o"),
                output_max_age_sec=0,
                command="true",
            )
        )


def test_validate_shell_flags_require_commands() -> None:
    with pytest.raises(ValueError, match="dns_check_use_shell=true requires dns_check_command"):
        _validate_target_rules(
            make_target(dns_check_use_shell=True, command="true"),
        )
    with pytest.raises(ValueError, match="gateway_check_use_shell=true requires"):
        _validate_target_rules(
            make_target(gateway_check_use_shell=True, command="true"),
        )
    with pytest.raises(ValueError, match="link_check_use_shell=true requires"):
        _validate_target_rules(
            make_target(link_check_use_shell=True, command="true"),
        )


def test_validate_network_probe_requires_interface_and_positive_gateway_timeout() -> None:
    with pytest.raises(ValueError, match="network_probe_enabled=true requires network_interface"):
        _validate_target_rules(
            make_target(network_probe_enabled=True, command="true"),
        )
    with pytest.raises(ValueError, match="gateway_probe_timeout_sec must be > 0"):
        _validate_target_rules(
            make_target(
                network_probe_enabled=True,
                network_interface="eth0",
                gateway_probe_timeout_sec=0,
                command="true",
            )
        )
    with pytest.raises(ValueError, match="internet_ip_targets must have at least one"):
        _validate_target_rules(
            make_target(
                network_probe_enabled=True,
                network_interface="eth0",
                internet_ip_targets=[],
                command="true",
            )
        )


def test_validate_consecutive_failure_threshold_order() -> None:
    with pytest.raises(ValueError, match="failed must be >= degraded"):
        _validate_target_rules(
            make_target(
                consecutive_failure_thresholds={"degraded": 5, "failed": 3},
                command="true",
            )
        )
    with pytest.raises(ValueError, match="values must be > 0"):
        _validate_target_rules(
            make_target(
                consecutive_failure_thresholds={"degraded": 0, "failed": 6},
                command="true",
            )
        )


def test_validate_reboot_vs_restart_thresholds() -> None:
    with pytest.raises(ValueError, match="reboot_threshold must be >= restart_threshold"):
        _validate_target_rules(
            make_target(
                restart_threshold=10,
                reboot_threshold=5,
                command="true",
            )
        )


def test_validate_service_active_requires_services() -> None:
    with pytest.raises(ValueError, match="services must list at least one"):
        _validate_target_rules(
            make_target(service_active=True, services=[], command="true"),
        )


def test_validate_stats_checks_require_stats_file() -> None:
    with pytest.raises(ValueError, match="stats_file is required when stats_"):
        _validate_target_rules(
            make_target(
                stats_updated_max_age_sec=120,
                stats_file=None,
                command="true",
            )
        )


def test_validate_maintenance_grace_and_shell() -> None:
    with pytest.raises(ValueError, match="maintenance_grace_sec must be >= 0"):
        _validate_target_rules(
            make_target(maintenance_grace_sec=-1, command="true"),
        )
    with pytest.raises(ValueError, match="maintenance_mode_use_shell=true requires"):
        _validate_target_rules(
            make_target(maintenance_mode_use_shell=True, command="true"),
        )


def test_warn_config_permissions_missing_file_is_silent(caplog: pytest.LogCaptureFixture) -> None:
    missing = Path("/nonexistent/raspi-sentinel-config-test.toml")
    with caplog.at_level(logging.WARNING):
        _warn_config_permissions(missing)
    assert caplog.records == []


def test_warn_config_permissions_group_or_world_writable_logs(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    p = tmp_path / "warn.toml"
    p.write_text("[global]\n", encoding="utf-8")
    p.chmod(stat.S_IRUSR | stat.S_IWUSR | stat.S_IWGRP | stat.S_IRGRP)
    with caplog.at_level(logging.WARNING):
        _warn_config_permissions(p)
    assert "group/world-writable" in caplog.text
    assert "warn.toml" in caplog.text


def _write_toml(path: Path, body: str) -> None:
    path.write_text(body.strip() + "\n", encoding="utf-8")


def _minimal_loadable_target(extra: str = 'command = "true"') -> str:
    return f"""
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
    {extra}
    """


def test_load_config_rejects_global_not_table(tmp_path: Path) -> None:
    conf = tmp_path / "c.toml"
    _write_toml(
        conf,
        """
        global = "bad"
        """,
    )
    with pytest.raises(ValueError, match="\\[global\\] must be a table"):
        load_config(conf)


def test_load_config_rejects_notify_not_table(tmp_path: Path) -> None:
    conf = tmp_path / "c.toml"
    _write_toml(
        conf,
        """
        notify = "bad"

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
        """,
    )
    with pytest.raises(ValueError, match=r"\[notify\] must be a table"):
        load_config(conf)


def test_load_config_rejects_discord_not_table(tmp_path: Path) -> None:
    conf = tmp_path / "c.toml"
    _write_toml(
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

        [notify]
        discord = "bad"
        """,
    )
    with pytest.raises(ValueError, match="\\[notify\\.discord\\] must be a table"):
        load_config(conf)


def test_load_config_rejects_discord_enabled_not_bool(tmp_path: Path) -> None:
    conf = tmp_path / "c.toml"
    _write_toml(
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
        enabled = "yes"
        username = "raspi-sentinel"
        timeout_sec = 5
        followup_delay_sec = 300
        heartbeat_interval_sec = 0
        """,
    )
    with pytest.raises(ValueError, match=r"\.enabled must be boolean"):
        load_config(conf)


def test_load_config_rejects_target_entry_not_table(tmp_path: Path) -> None:
    conf = tmp_path / "c.toml"
    _write_toml(
        conf,
        """
        targets = [1]

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
        """,
    )
    with pytest.raises(ValueError, match=r"Each \[\[targets\]\] entry must be a table"):
        load_config(conf)


def test_load_config_rejects_consecutive_failure_thresholds_not_table(tmp_path: Path) -> None:
    conf = tmp_path / "c.toml"
    _write_toml(
        conf,
        _minimal_loadable_target(
            extra='command = "true"\n    consecutive_failure_thresholds = "bad"',
        ),
    )
    with pytest.raises(ValueError, match="consecutive_failure_thresholds must be a table"):
        load_config(conf)


def test_load_config_rejects_latency_thresholds_not_table(tmp_path: Path) -> None:
    conf = tmp_path / "c.toml"
    _write_toml(
        conf,
        _minimal_loadable_target(
            extra='command = "true"\n    latency_thresholds_ms = "bad"',
        ),
    )
    with pytest.raises(ValueError, match="latency_thresholds_ms must be a table"):
        load_config(conf)


def test_load_config_rejects_packet_loss_thresholds_not_table(tmp_path: Path) -> None:
    conf = tmp_path / "c.toml"
    _write_toml(
        conf,
        _minimal_loadable_target(
            extra='command = "true"\n    packet_loss_thresholds_pct = "bad"',
        ),
    )
    with pytest.raises(ValueError, match="packet_loss_thresholds_pct must be a table"):
        load_config(conf)
