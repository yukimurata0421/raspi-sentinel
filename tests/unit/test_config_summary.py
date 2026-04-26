from __future__ import annotations

from pathlib import Path
from typing import Any

from raspi_sentinel import config_summary
from raspi_sentinel.config import load_config


def _write(path: Path, text: str) -> None:
    path.write_text(text.strip() + "\n", encoding="utf-8")


def _config_text(extra_target: str = 'command = "true"') -> str:
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
    {extra_target}
    """


def test_config_summary_reports_permission_warning(tmp_path: Path, monkeypatch: Any) -> None:
    conf = tmp_path / "config.toml"
    _write(conf, _config_text())
    conf.chmod(0o666)

    cfg = load_config(conf)
    monkeypatch.setattr(
        config_summary,
        "_check_service_unit_load_state",
        lambda unit, timeout_sec=3: None,
    )

    report = config_summary.build_config_validation_report(config_path=conf, config=cfg)
    warning = report.get("config_permission_warning")
    assert isinstance(warning, str)
    assert "group/world-writable" in warning


def test_format_config_summary_includes_shell_target_and_target_block(
    tmp_path: Path, monkeypatch: Any
) -> None:
    conf = tmp_path / "config.toml"
    target_rules = "\n".join(
        (
            'command = "true"',
            "command_use_shell = true",
            'heartbeat_file = "/tmp/missing"',
            "heartbeat_max_age_sec = 30",
        )
    )
    _write(
        conf,
        _config_text(extra_target=target_rules),
    )

    cfg = load_config(conf)
    monkeypatch.setattr(
        config_summary,
        "_check_service_unit_load_state",
        lambda unit, timeout_sec=3: "loaded",
    )

    report = config_summary.build_config_validation_report(config_path=conf, config=cfg)
    text = config_summary.format_config_validation_report(report)

    assert "Config validation: OK" in text
    assert "targets using shell commands: demo" in text
    assert "[demo]" in text
    assert "rules:" in text
    assert "shell_opt_in_checks: command" in text


def test_config_summary_adds_global_threshold_warning(tmp_path: Path, monkeypatch: Any) -> None:
    conf = tmp_path / "config.toml"
    _write(
        conf,
        """
        [global]
        state_file = "/tmp/state.json"
        restart_threshold = 5
        reboot_threshold = 5
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
    monkeypatch.setattr(
        config_summary,
        "_check_service_unit_load_state",
        lambda unit, timeout_sec=3: "loaded",
    )
    report = config_summary.build_config_validation_report(config_path=conf, config=cfg)
    assert any(
        "restart_threshold should be lower than reboot_threshold" in warning
        for warning in report.get("global_warnings", [])
    )


def test_config_summary_warns_when_require_tmpfs_uses_non_run_state_path(
    tmp_path: Path, monkeypatch: Any
) -> None:
    conf = tmp_path / "config.toml"
    _write(
        conf,
        """
        [global]
        state_file = "/var/lib/raspi-sentinel/state.json"
        restart_threshold = 2
        reboot_threshold = 3
        restart_cooldown_sec = 10
        reboot_cooldown_sec = 20
        reboot_window_sec = 300
        max_reboots_in_window = 2
        min_uptime_for_reboot_sec = 60
        default_command_timeout_sec = 5
        loop_interval_sec = 30

        [storage]
        require_tmpfs = true

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
    monkeypatch.setattr(
        config_summary,
        "_check_service_unit_load_state",
        lambda unit, timeout_sec=3: "loaded",
    )
    report = config_summary.build_config_validation_report(config_path=conf, config=cfg)
    assert any(
        "storage.require_tmpfs=true but state_volatile path is not under /run" in warning
        for warning in report.get("global_warnings", [])
    )


def test_config_summary_does_not_warn_when_require_tmpfs_uses_run_state_path(
    tmp_path: Path, monkeypatch: Any
) -> None:
    conf = tmp_path / "config.toml"
    _write(
        conf,
        """
        [global]
        state_file = "/run/raspi-sentinel/state.volatile.json"
        restart_threshold = 2
        reboot_threshold = 3
        restart_cooldown_sec = 10
        reboot_cooldown_sec = 20
        reboot_window_sec = 300
        max_reboots_in_window = 2
        min_uptime_for_reboot_sec = 60
        default_command_timeout_sec = 5
        loop_interval_sec = 30

        [storage]
        require_tmpfs = true

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
    monkeypatch.setattr(
        config_summary,
        "_check_service_unit_load_state",
        lambda unit, timeout_sec=3: "loaded",
    )
    report = config_summary.build_config_validation_report(config_path=conf, config=cfg)
    assert not any(
        "storage.require_tmpfs=true but state_volatile path is not under /run" in warning
        for warning in report.get("global_warnings", [])
    )


def test_check_service_unit_load_state_returns_none_when_systemctl_missing(
    monkeypatch: Any,
) -> None:
    def raise_not_found(*args: Any, **kwargs: Any) -> None:
        del args
        del kwargs
        raise FileNotFoundError("systemctl not found")

    monkeypatch.setattr(config_summary.subprocess, "run", raise_not_found)
    assert config_summary._check_service_unit_load_state("demo.service") is None


def test_check_service_unit_load_state_returns_unknown_on_oserror(monkeypatch: Any) -> None:
    def raise_oserror(*args: Any, **kwargs: Any) -> None:
        del args
        del kwargs
        raise OSError("permission denied")

    monkeypatch.setattr(config_summary.subprocess, "run", raise_oserror)
    assert config_summary._check_service_unit_load_state("demo.service") == "unknown"


def test_count_warnings_ignores_non_list_target_warning_field() -> None:
    count = config_summary._count_warnings_from_target_summaries(
        target_summaries=[{"warnings": "not-a-list"}, {"warnings": ["a", "b"]}],
        config_permission_warning="warn",
        global_warnings=["g1"],
    )
    assert count == 4
