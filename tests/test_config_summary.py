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
