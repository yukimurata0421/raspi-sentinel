from __future__ import annotations

from pathlib import Path

import pytest

from raspi_sentinel.config import load_config


def _write(path: Path, text: str) -> None:
    path.write_text(text.strip() + "\n", encoding="utf-8")


def _base_config(extra_target: str = 'command = "true"') -> str:
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
    retry_interval_sec = 60
    retry_backoff_base_sec = 0.5
    heartbeat_interval_sec = 0

    [[targets]]
    name = "demo"
    services = []
    service_active = false
    {extra_target}
    """


def test_duplicate_target_name_rejected(tmp_path: Path) -> None:
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

        [[targets]]
        name = "demo"
        services = []
        service_active = false
        command = "true"
        """,
    )
    with pytest.raises(ValueError, match="duplicate target name"):
        load_config(conf)


def test_duplicate_target_name_after_strip_rejected(tmp_path: Path) -> None:
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

        [[targets]]
        name = " demo "
        services = []
        service_active = false
        command = "true"
        """,
    )
    with pytest.raises(ValueError, match="duplicate target name"):
        load_config(conf)


def test_discord_enabled_requires_webhook(tmp_path: Path) -> None:
    conf = tmp_path / "config.toml"
    _write(
        conf,
        _base_config().replace("enabled = false", "enabled = true"),
    )
    with pytest.raises(ValueError, match="webhook_url is required"):
        load_config(conf)


def test_retry_interval_sec_must_be_positive(tmp_path: Path) -> None:
    conf = tmp_path / "config.toml"
    _write(
        conf,
        _base_config().replace("retry_interval_sec = 60", "retry_interval_sec = 0"),
    )
    with pytest.raises(ValueError, match="retry_interval_sec must be > 0"):
        load_config(conf)


def test_retry_backoff_base_sec_must_be_non_negative(tmp_path: Path) -> None:
    conf = tmp_path / "config.toml"
    _write(
        conf,
        _base_config().replace("retry_backoff_base_sec = 0.5", "retry_backoff_base_sec = -1"),
    )
    with pytest.raises(ValueError, match="retry_backoff_base_sec must be >= 0"):
        load_config(conf)


def test_target_requires_at_least_one_rule(tmp_path: Path) -> None:
    conf = tmp_path / "config.toml"
    _write(conf, _base_config(extra_target=""))
    with pytest.raises(ValueError, match="at least one rule is required"):
        load_config(conf)


def test_invalid_global_thresholds_rejected(tmp_path: Path) -> None:
    conf = tmp_path / "config.toml"
    _write(conf, _base_config().replace("restart_threshold = 2", "restart_threshold = 0"))
    with pytest.raises(ValueError, match="global restart_threshold must be > 0"):
        load_config(conf)


def test_restart_service_timeout_sec_must_be_positive(tmp_path: Path) -> None:
    conf = tmp_path / "config.toml"
    bad = _base_config().replace(
        "loop_interval_sec = 30",
        "loop_interval_sec = 30\n    restart_service_timeout_sec = 0",
    )
    _write(
        conf,
        bad,
    )
    with pytest.raises(ValueError, match="global restart_service_timeout_sec must be > 0"):
        load_config(conf)


def test_global_reboot_threshold_must_be_greater_than_restart(tmp_path: Path) -> None:
    conf = tmp_path / "config.toml"
    _write(conf, _base_config().replace("reboot_threshold = 3", "reboot_threshold = 2"))
    with pytest.raises(ValueError, match="global reboot_threshold must be > restart_threshold"):
        load_config(conf)


def test_services_must_not_include_blank_entries(tmp_path: Path) -> None:
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
        services = ["svc", " "]
        service_active = true
        command = "true"
        """,
    )
    with pytest.raises(ValueError, match="services must not contain empty names"):
        load_config(conf)


def test_target_can_use_time_health_as_single_rule(tmp_path: Path) -> None:
    conf = tmp_path / "config.toml"
    _write(
        conf,
        _base_config(extra_target="time_health_enabled = true"),
    )
    cfg = load_config(conf)
    assert cfg.targets[0].time_health_enabled is True


def test_check_interval_threshold_must_be_positive(tmp_path: Path) -> None:
    conf = tmp_path / "config.toml"
    _write(
        conf,
        _base_config(
            extra_target="time_health_enabled = true\n    check_interval_threshold_sec = 0"
        ),
    )
    with pytest.raises(ValueError, match="check_interval_threshold_sec must be > 0"):
        load_config(conf)


def test_command_use_shell_requires_command(tmp_path: Path) -> None:
    conf = tmp_path / "config.toml"
    _write(
        conf,
        _base_config(extra_target="command_use_shell = true"),
    )
    with pytest.raises(ValueError, match="command_use_shell=true requires command"):
        load_config(conf)


def test_external_status_thresholds_require_status_file(tmp_path: Path) -> None:
    conf = tmp_path / "config.toml"
    _write(
        conf,
        _base_config(extra_target="external_status_updated_max_age_sec = 120"),
    )
    with pytest.raises(ValueError, match="external_status_file is required"):
        load_config(conf)


def test_external_status_startup_grace_must_be_non_negative(tmp_path: Path) -> None:
    conf = tmp_path / "config.toml"
    _write(
        conf,
        _base_config(
            extra_target=(
                'external_status_file = "/tmp/s.json"\n'
                "    external_status_updated_max_age_sec = 120\n"
                "    external_status_startup_grace_sec = -1"
            )
        ),
    )
    with pytest.raises(ValueError, match="external_status_startup_grace_sec must be >= 0"):
        load_config(conf)
