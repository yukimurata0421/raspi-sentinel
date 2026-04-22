from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from raspi_sentinel import cli, config_summary
from raspi_sentinel import recovery as recovery_module
from raspi_sentinel.checks import CheckFailure, CheckResult
from raspi_sentinel.config import load_config
from raspi_sentinel.exit_codes import STORAGE_VERIFY_FAILED
from raspi_sentinel.status_events import classify_target_reason, classify_target_status
from raspi_sentinel.storage_verify import StorageVerifyResult


def _write(path: Path, text: str) -> None:
    path.write_text(text.strip() + "\n", encoding="utf-8")


def test_classify_status_prioritizes_dependency_types() -> None:
    assert (
        classify_target_status(CheckResult("t", False, [CheckFailure("dependency_dns", "x")]))
        == "degraded"
    )
    assert (
        classify_target_reason(CheckResult("t", False, [CheckFailure("dependency_dns", "x")]))
        == "dns_error"
    )
    assert (
        classify_target_status(CheckResult("t", False, [CheckFailure("dependency_gateway", "x")]))
        == "degraded"
    )
    assert (
        classify_target_reason(CheckResult("t", False, [CheckFailure("dependency_gateway", "x")]))
        == "gateway_error"
    )
    assert (
        classify_target_status(
            CheckResult("t", False, [CheckFailure("semantic_last_success_ts", "x")])
        )
        == "degraded"
    )


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


def test_run_once_json_outputs_machine_readable_cycle_summary(
    tmp_path: Path, monkeypatch: Any, capsys: Any
) -> None:
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
    monkeypatch.setattr(cli.time, "time", lambda: 1_000.0)

    rc = cli.main(["-c", str(conf), "--dry-run", "run-once", "--json"])
    assert rc == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["overall_status"] == "ok"
    assert payload["dry_run"] is True
    assert payload["reboot_requested"] is False
    assert payload["targets"]["self_test"]["status"] == "ok"
    assert payload["targets"]["self_test"]["reason"] == "healthy"
    assert payload["targets"]["self_test"]["action"] == "none"


def test_validate_config_json_includes_rules_warnings_and_shell_targets(
    tmp_path: Path, monkeypatch: Any, capsys: Any
) -> None:
    conf = tmp_path / "config.toml"
    heartbeat_missing = tmp_path / "missing-heartbeat.txt"
    _write(
        conf,
        f"""
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
        heartbeat_file = "{heartbeat_missing}"
        heartbeat_max_age_sec = 60
        """,
    )
    monkeypatch.setattr(
        config_summary,
        "_check_service_unit_load_state",
        lambda unit, timeout_sec=3: "loaded",
    )

    rc = cli.main(["-c", str(conf), "validate-config", "--json"])
    assert rc == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["config_path"] == str(conf)
    assert payload["shell_command_targets"] == ["demo"]
    target = payload["targets"][0]
    assert target["name"] == "demo"
    assert "command" in target["enabled_rules"]
    assert target["shell_commands"]["command"] == "true"
    assert any("heartbeat_file path does not exist now" in msg for msg in target["warnings"])


def test_validate_config_returns_error_code_for_invalid_config(tmp_path: Path) -> None:
    conf = tmp_path / "invalid.toml"
    _write(
        conf,
        """
        [global]
        state_file = "/tmp/state.json"

        [notify.discord]
        enabled = false
        username = "raspi-sentinel"
        timeout_sec = 5
        followup_delay_sec = 300
        heartbeat_interval_sec = 0
        """,
    )

    rc = cli.main(["-c", str(conf), "validate-config"])
    assert rc == 10


def test_verify_storage_returns_success_json(tmp_path: Path, monkeypatch: Any, capsys: Any) -> None:
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
    monkeypatch.setattr(
        cli,
        "verify_tmpfs_storage",
        lambda **kwargs: StorageVerifyResult(
            ok=True,
            mount_path=Path("/run/raspi-sentinel"),
            mount_fs_type="tmpfs",
            owner_uid=0,
            owner_gid=0,
            mode=0o755,
            free_bytes=1024 * 1024,
        ),
    )
    rc = cli.main(["-c", str(conf), "verify-storage", "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["mount_fs_type"] == "tmpfs"


def test_verify_storage_returns_failure_code(tmp_path: Path, monkeypatch: Any) -> None:
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
    monkeypatch.setattr(
        cli,
        "verify_tmpfs_storage",
        lambda **kwargs: StorageVerifyResult(
            ok=False,
            mount_path=Path("/run/raspi-sentinel"),
            mount_fs_type="ext4",
            owner_uid=0,
            owner_gid=0,
            mode=0o755,
            free_bytes=1024,
            reason="mount fs type is not tmpfs",
        ),
    )
    rc = cli.main(["-c", str(conf), "verify-storage"])
    assert rc == STORAGE_VERIFY_FAILED


def test_validate_config_strict_returns_nonzero_on_warnings(
    tmp_path: Path, monkeypatch: Any
) -> None:
    conf = tmp_path / "config.toml"
    missing = tmp_path / "missing.txt"
    _write(
        conf,
        f"""
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
        heartbeat_file = "{missing}"
        heartbeat_max_age_sec = 60
        """,
    )
    monkeypatch.setattr(
        config_summary,
        "_check_service_unit_load_state",
        lambda unit, timeout_sec=3: "loaded",
    )

    rc = cli.main(["-c", str(conf), "validate-config", "--strict"])
    assert rc == 15


def test_full_cycle_unhealthy_target_restarts_and_logs(tmp_path: Path, monkeypatch: Any) -> None:
    conf = tmp_path / "config.toml"
    state_file = tmp_path / "state.json"
    events_file = tmp_path / "events.jsonl"
    monitor_stats_file = tmp_path / "stats.json"
    _write(
        conf,
        f"""
        [global]
        state_file = "{state_file}"
        state_lock_timeout_sec = 1
        events_file = "{events_file}"
        events_max_file_bytes = 100000
        events_backup_generations = 2
        monitor_stats_file = "{monitor_stats_file}"
        monitor_stats_interval_sec = 30
        restart_threshold = 1
        reboot_threshold = 9
        restart_cooldown_sec = 0
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
        name = "demo"
        services = ["demo.service"]
        service_active = true
        """,
    )
    cfg = load_config(conf)

    monkeypatch.setattr(cli.time, "time", lambda: 1000.0)
    monkeypatch.setattr(cli.time, "monotonic", lambda: 500.0)

    restart_calls: list[list[str]] = []

    def fake_restart_services(services: list[str], dry_run: bool) -> bool:
        restart_calls.append(["systemctl", "restart", *services])
        return True

    monkeypatch.setattr(recovery_module, "_restart_services", fake_restart_services)

    rc, report = cli._run_cycle_collect(config=cfg, dry_run=False)

    assert rc == 1
    assert report["targets"]["demo"]["action"] == "restart"
    assert report["targets"]["demo"]["status"] == "failed"
    assert restart_calls and restart_calls[0] == ["systemctl", "restart", "demo.service"]
    events_text = events_file.read_text(encoding="utf-8")
    assert '"action": "restart"' in events_text
    saved = json.loads(state_file.read_text(encoding="utf-8"))
    assert saved["targets"]["demo"]["consecutive_failures"] == 1
    assert saved["targets"]["demo"]["last_action"] == "restart"


def test_state_corruption_enters_limited_mode_and_blocks_disruptive_actions(
    tmp_path: Path, monkeypatch: Any
) -> None:
    conf = tmp_path / "config.toml"
    state_file = tmp_path / "state.json"
    events_file = tmp_path / "events.jsonl"
    monitor_stats_file = tmp_path / "stats.json"
    _write(
        conf,
        f"""
        [global]
        state_file = "{state_file}"
        state_lock_timeout_sec = 1
        events_file = "{events_file}"
        events_max_file_bytes = 100000
        events_backup_generations = 2
        monitor_stats_file = "{monitor_stats_file}"
        monitor_stats_interval_sec = 30
        restart_threshold = 1
        reboot_threshold = 2
        restart_cooldown_sec = 0
        reboot_cooldown_sec = 0
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
        name = "demo"
        services = ["demo.service"]
        service_active = true
        """,
    )
    cfg = load_config(conf)
    state_file.write_text("{not-json", encoding="utf-8")

    monkeypatch.setattr(cli.time, "time", lambda: 1234.0)
    monkeypatch.setattr(cli.time, "monotonic", lambda: 567.0)

    restart_calls: list[list[str]] = []

    def fake_restart_services(services: list[str], dry_run: bool) -> bool:
        restart_calls.append(["systemctl", "restart", *services])
        return True

    monkeypatch.setattr(recovery_module, "_restart_services", fake_restart_services)

    rc, report = cli._run_cycle_collect(config=cfg, dry_run=False)
    assert rc == 1
    assert report["limited_mode"] is True
    assert "invalid JSON" in str(report["state_issue"])
    assert report["targets"]["demo"]["action"] == "warn"
    assert restart_calls == []

    backup_paths = sorted(tmp_path.glob("state.json.corrupt.*"))
    assert backup_paths, "corrupted state should be quarantined"

    events_text = events_file.read_text(encoding="utf-8")
    assert '"kind": "state_corrupted"' in events_text


def test_state_corruption_limited_mode_still_sends_warn_notification(
    tmp_path: Path, monkeypatch: Any
) -> None:
    conf = tmp_path / "config.toml"
    state_file = tmp_path / "state.json"
    events_file = tmp_path / "events.jsonl"
    monitor_stats_file = tmp_path / "stats.json"
    _write(
        conf,
        f"""
        [global]
        state_file = "{state_file}"
        state_lock_timeout_sec = 1
        events_file = "{events_file}"
        events_max_file_bytes = 100000
        events_backup_generations = 2
        monitor_stats_file = "{monitor_stats_file}"
        monitor_stats_interval_sec = 30
        restart_threshold = 1
        reboot_threshold = 2
        restart_cooldown_sec = 0
        reboot_cooldown_sec = 0
        reboot_window_sec = 300
        max_reboots_in_window = 2
        min_uptime_for_reboot_sec = 0
        default_command_timeout_sec = 2
        loop_interval_sec = 30

        [notify.discord]
        enabled = true
        webhook_url = "https://example.invalid/webhook"
        username = "raspi-sentinel"
        timeout_sec = 5
        followup_delay_sec = 300
        heartbeat_interval_sec = 0

        [[targets]]
        name = "demo"
        services = []
        service_active = false
        command = "echo ok | cat"
        command_use_shell = false
        """,
    )
    cfg = load_config(conf)
    state_file.write_text("{not-json", encoding="utf-8")

    monkeypatch.setattr(cli.time, "time", lambda: 2222.0)
    monkeypatch.setattr(cli.time, "monotonic", lambda: 888.0)

    restart_calls: list[list[str]] = []

    def fake_restart_services(services: list[str], dry_run: bool) -> bool:
        restart_calls.append(["systemctl", "restart", *services])
        return True

    sent_notifications: list[dict[str, object]] = []

    def fake_send_lines(self: Any, title: str, lines: list[str], severity: str = "INFO") -> bool:
        sent_notifications.append(
            {
                "title": title,
                "lines": lines,
                "severity": severity,
            }
        )
        return True

    monkeypatch.setattr(recovery_module, "_restart_services", fake_restart_services)
    monkeypatch.setattr("raspi_sentinel.notify.DiscordNotifier.send_lines", fake_send_lines)

    rc, report = cli._run_cycle_collect(config=cfg, dry_run=False)

    assert rc == 1
    assert report["limited_mode"] is True
    assert report["targets"]["demo"]["action"] == "none"
    assert restart_calls == []
    assert sent_notifications == []

    saved = json.loads(state_file.read_text(encoding="utf-8"))
    assert "demo" not in saved["followups"]
    events_text = events_file.read_text(encoding="utf-8")
    assert '"kind": "state_corrupted"' in events_text


def test_shell_syntax_without_opt_in_runs_without_shell(tmp_path: Path, monkeypatch: Any) -> None:
    conf = tmp_path / "config.toml"
    state_file = tmp_path / "state.json"
    events_file = tmp_path / "events.jsonl"
    monitor_stats_file = tmp_path / "stats.json"
    _write(
        conf,
        f"""
        [global]
        state_file = "{state_file}"
        state_lock_timeout_sec = 1
        events_file = "{events_file}"
        events_max_file_bytes = 100000
        events_backup_generations = 2
        monitor_stats_file = "{monitor_stats_file}"
        monitor_stats_interval_sec = 30
        restart_threshold = 5
        reboot_threshold = 9
        restart_cooldown_sec = 0
        reboot_cooldown_sec = 0
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
        name = "demo"
        services = []
        service_active = false
        command = "echo ok | cat"
        command_use_shell = false
        """,
    )
    cfg = load_config(conf)

    monkeypatch.setattr(cli.time, "time", lambda: 3333.0)
    monkeypatch.setattr(cli.time, "monotonic", lambda: 999.0)

    calls: list[tuple[object, object]] = []

    def fake_run(*args: object, **kwargs: object) -> object:
        calls.append((args, kwargs))
        return subprocess.CompletedProcess(args=args[0], returncode=0, stdout="", stderr="")

    monkeypatch.setattr("raspi_sentinel.checks.subprocess.run", fake_run)

    rc, report = cli._run_cycle_collect(config=cfg, dry_run=False)
    assert rc == 0
    assert report["targets"]["demo"]["status"] == "ok"
    assert report["targets"]["demo"]["reason"] == "healthy"
    assert report["targets"]["demo"]["action"] == "none"

    saved = json.loads(state_file.read_text(encoding="utf-8"))
    assert saved["targets"]["demo"]["consecutive_failures"] == 0
    assert saved["targets"]["demo"]["last_action"] == "none"
    assert calls
    assert calls[0][1]["shell"] is False


def test_run_once_reports_state_lock_timeout_for_timer_service(
    tmp_path: Path, monkeypatch: Any, capsys: Any
) -> None:
    conf = tmp_path / "config.toml"
    _write(
        conf,
        """
        [global]
        state_file = "/tmp/raspi-sentinel-test-lock-timeout.json"
        state_lock_timeout_sec = 1
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
        name = "demo"
        services = []
        service_active = false
        command = "true"
        """,
    )

    def timeout_lock(self: Any, timeout_sec: int = 5) -> None:
        raise TimeoutError(f"state lock timeout after {timeout_sec}s: forced for test")

    monkeypatch.setattr("raspi_sentinel.engine.TieredStateStore.exclusive_lock", timeout_lock)

    rc = cli.main(["-c", str(conf), "run-once", "--json"])
    assert rc == 13
    payload = json.loads(capsys.readouterr().out)
    assert payload["reason"] == "state_lock_timeout"
    assert payload["overall_status"] == "failed"
    assert payload["targets"] == {}
    assert payload["state_persisted"] is False
