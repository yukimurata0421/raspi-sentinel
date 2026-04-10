from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from raspi_sentinel.checks import run_checks
from raspi_sentinel.config import TargetConfig
from raspi_sentinel.status_events import classify_target_reason, classify_target_status


def _target(**overrides: object) -> TargetConfig:
    base = {
        "name": "demo",
        "services": [],
        "service_active": False,
        "heartbeat_file": None,
        "heartbeat_max_age_sec": None,
        "output_file": None,
        "output_max_age_sec": None,
        "command": None,
        "command_use_shell": False,
        "command_timeout_sec": None,
        "dns_check_command": None,
        "dns_check_use_shell": False,
        "gateway_check_command": None,
        "gateway_check_use_shell": False,
        "dependency_check_timeout_sec": None,
        "stats_file": None,
        "stats_updated_max_age_sec": None,
        "stats_last_input_max_age_sec": None,
        "stats_last_success_max_age_sec": None,
        "stats_records_stall_cycles": None,
        "time_health_enabled": False,
        "check_interval_threshold_sec": 30,
        "wall_clock_freeze_min_monotonic_sec": 25,
        "wall_clock_freeze_max_wall_advance_sec": 1,
        "wall_clock_drift_threshold_sec": 30,
        "http_time_probe_url": None,
        "http_time_probe_timeout_sec": 5,
        "clock_skew_threshold_sec": 300,
        "clock_anomaly_reboot_consecutive": 3,
        "maintenance_mode_command": None,
        "maintenance_mode_use_shell": False,
        "maintenance_mode_timeout_sec": None,
        "maintenance_grace_sec": None,
        "restart_threshold": None,
        "reboot_threshold": None,
    }
    base.update(overrides)
    return TargetConfig(**base)


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_stats_updated_at_stale_is_semantic_failure_and_degraded(
    tmp_path: Path,
) -> None:
    now = datetime.now(timezone.utc)
    stats_path = tmp_path / "stats.json"
    _write_json(
        stats_path,
        {
            "updated_at": (now - timedelta(minutes=10)).isoformat(),
            "last_success_ts": now.isoformat(),
            "status": "ok",
            "records_processed_total": 123,
        },
    )
    result = run_checks(
        _target(
            stats_file=stats_path,
            stats_updated_max_age_sec=60,
        )
    )
    assert not result.healthy
    assert any(f.check == "semantic_updated_at" for f in result.failures)
    assert classify_target_status(result) == "degraded"
    assert classify_target_reason(result) == "stats_stale"


def test_last_input_fresh_last_success_stale_is_processing_failure(
    tmp_path: Path,
) -> None:
    now = datetime.now(timezone.utc)
    stats_path = tmp_path / "stats.json"
    _write_json(
        stats_path,
        {
            "updated_at": now.isoformat(),
            "last_input_ts": now.isoformat(),
            "last_success_ts": (now - timedelta(minutes=30)).isoformat(),
            "status": "ok",
            "records_processed_total": 999,
        },
    )
    result = run_checks(
        _target(
            stats_file=stats_path,
            stats_last_input_max_age_sec=120,
            stats_last_success_max_age_sec=120,
        )
    )
    assert not result.healthy
    assert any(f.check == "semantic_last_success_ts" for f in result.failures)
    assert not any(f.check == "semantic_last_input_ts" for f in result.failures)


def test_invalid_json_is_treated_as_unhealthy(tmp_path: Path) -> None:
    stats_path = tmp_path / "stats.json"
    stats_path.write_text("{broken", encoding="utf-8")
    result = run_checks(
        _target(
            stats_file=stats_path,
            stats_updated_max_age_sec=60,
        )
    )
    assert not result.healthy
    assert any(f.check == "semantic_stats_file" for f in result.failures)


def test_missing_required_stats_field_is_treated_as_unhealthy(tmp_path: Path) -> None:
    stats_path = tmp_path / "stats.json"
    _write_json(stats_path, {"status": "ok"})
    result = run_checks(
        _target(
            stats_file=stats_path,
            stats_updated_max_age_sec=60,
        )
    )
    assert not result.healthy
    assert any(f.check == "semantic_updated_at" for f in result.failures)
