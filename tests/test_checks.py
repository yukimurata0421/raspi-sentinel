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
        "dns_server_check_command": None,
        "dns_server_check_use_shell": False,
        "gateway_check_command": None,
        "gateway_check_use_shell": False,
        "link_check_command": None,
        "link_check_use_shell": False,
        "default_route_check_command": None,
        "default_route_check_use_shell": False,
        "internet_ip_check_command": None,
        "internet_ip_check_use_shell": False,
        "wan_vs_target_check_command": None,
        "wan_vs_target_check_use_shell": False,
        "network_probe_enabled": False,
        "network_interface": None,
        "gateway_probe_timeout_sec": 2,
        "internet_ip_targets": ["1.1.1.1", "8.8.8.8"],
        "dns_query_target": None,
        "http_probe_target": None,
        "consecutive_failure_thresholds": {"degraded": 2, "failed": 6},
        "latency_thresholds_ms": {},
        "packet_loss_thresholds_pct": {},
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


def test_external_status_json_healthy_is_treated_as_healthy(tmp_path: Path) -> None:
    now = datetime.now(timezone.utc)
    status_path = tmp_path / "service-status.json"
    _write_json(
        status_path,
        {
            "updated_at": now.isoformat(),
            "internal_state": "healthy",
            "last_progress_ts": now.isoformat(),
            "last_success_ts": now.isoformat(),
            "reason": "service_specific_detail",
            "components": {"pubsub": {"status": "stalled"}},
        },
    )

    result = run_checks(
        _target(
            external_status_file=status_path,
            external_status_updated_max_age_sec=120,
            external_status_last_progress_max_age_sec=120,
            external_status_last_success_max_age_sec=120,
        )
    )
    assert result.healthy
    assert not result.failures


def test_external_status_updated_at_stale_is_unhealthy(tmp_path: Path) -> None:
    now = datetime.now(timezone.utc)
    status_path = tmp_path / "service-status.json"
    _write_json(
        status_path,
        {
            "updated_at": (now - timedelta(minutes=10)).isoformat(),
            "internal_state": "healthy",
            "last_progress_ts": now.isoformat(),
            "last_success_ts": now.isoformat(),
        },
    )

    result = run_checks(
        _target(
            external_status_file=status_path,
            external_status_updated_max_age_sec=60,
        )
    )
    assert not result.healthy
    assert any(f.check == "semantic_external_updated_at" for f in result.failures)


def test_external_status_last_progress_stale_is_unhealthy(tmp_path: Path) -> None:
    now = datetime.now(timezone.utc)
    status_path = tmp_path / "service-status.json"
    _write_json(
        status_path,
        {
            "updated_at": now.isoformat(),
            "internal_state": "healthy",
            "last_progress_ts": (now - timedelta(minutes=20)).isoformat(),
            "last_success_ts": now.isoformat(),
        },
    )

    result = run_checks(
        _target(
            external_status_file=status_path,
            external_status_last_progress_max_age_sec=60,
            external_status_startup_grace_sec=0,
        )
    )
    assert not result.healthy
    assert any(f.check == "semantic_external_last_progress_ts" for f in result.failures)


def test_external_status_internal_state_failed_is_unhealthy(tmp_path: Path) -> None:
    now = datetime.now(timezone.utc)
    status_path = tmp_path / "service-status.json"
    _write_json(
        status_path,
        {
            "updated_at": now.isoformat(),
            "internal_state": "failed",
            "last_progress_ts": now.isoformat(),
            "last_success_ts": now.isoformat(),
            "reason": "amazon_specific_reason",
            "components": {"pubsub": {"status": "failed"}},
        },
    )

    result = run_checks(
        _target(
            external_status_file=status_path,
            external_status_updated_max_age_sec=120,
            external_status_last_progress_max_age_sec=120,
            external_status_last_success_max_age_sec=120,
        )
    )
    assert not result.healthy
    assert any(f.check == "semantic_external_internal_state" for f in result.failures)


def test_external_status_missing_or_broken_file_is_unhealthy(tmp_path: Path) -> None:
    missing = tmp_path / "missing-status.json"
    result_missing = run_checks(
        _target(
            external_status_file=missing,
            external_status_updated_max_age_sec=120,
        )
    )
    assert not result_missing.healthy
    assert any(f.check == "semantic_external_status_file" for f in result_missing.failures)

    broken = tmp_path / "broken-status.json"
    broken.write_text("{broken", encoding="utf-8")
    result_broken = run_checks(
        _target(
            external_status_file=broken,
            external_status_updated_max_age_sec=120,
        )
    )
    assert not result_broken.healthy
    assert any(f.check == "semantic_external_status_file" for f in result_broken.failures)


def test_external_status_startup_grace_allows_null_progress_and_success(tmp_path: Path) -> None:
    now = datetime.now(timezone.utc)
    status_path = tmp_path / "service-status.json"
    _write_json(
        status_path,
        {
            "updated_at": now.isoformat(),
            "internal_state": "degraded",
            "last_progress_ts": None,
            "last_success_ts": None,
        },
    )

    result = run_checks(
        _target(
            external_status_file=status_path,
            external_status_updated_max_age_sec=120,
            external_status_last_progress_max_age_sec=60,
            external_status_last_success_max_age_sec=60,
            external_status_startup_grace_sec=180,
        )
    )
    assert not any(
        f.check in ("semantic_external_last_progress_ts", "semantic_external_last_success_ts")
        for f in result.failures
    )
