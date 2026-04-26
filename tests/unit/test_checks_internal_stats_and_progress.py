from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from checks_internal_branches_helpers import target

from raspi_sentinel import checks


def test_stats_schema_branches_for_invalid_fields(tmp_path: Path) -> None:
    now = datetime.now(timezone.utc).isoformat()
    stats = {
        "updated_at": now,
        "last_input_ts": now,
        "last_success_ts": now,
        "status": 1,
        "records_processed_total": "x",
        "dns_ok": "x",
        "gateway_ok": "x",
    }
    p = tmp_path / "stats.json"
    p.write_text(json.dumps(stats), encoding="utf-8")
    result = checks.run_checks(
        target(
            stats_file=p,
            stats_updated_max_age_sec=120,
            stats_last_input_max_age_sec=120,
            stats_last_success_max_age_sec=120,
        )
    )
    names = {f.check for f in result.failures}
    assert "semantic_status" in names
    assert "semantic_records_total" in names
    assert "dependency_dns" in names
    assert "dependency_gateway" in names


def test_stats_schema_marks_unhealthy_status_and_false_dependency_flags(
    tmp_path: Path,
) -> None:
    now = datetime.now(timezone.utc).isoformat()
    p = tmp_path / "stats.json"
    p.write_text(
        json.dumps(
            {
                "updated_at": now,
                "status": "degraded",
                "dns_ok": False,
                "gateway_ok": False,
            }
        ),
        encoding="utf-8",
    )
    result = checks.run_checks(target(stats_file=p, stats_updated_max_age_sec=120))
    names = {f.check for f in result.failures}
    assert "semantic_status" in names
    assert "dependency_dns" in names
    assert "dependency_gateway" in names


def test_stats_file_read_oserror_and_non_object(tmp_path: Path) -> None:
    d = tmp_path / "dir_as_stats"
    d.mkdir()
    result = checks.run_checks(target(stats_file=d, stats_updated_max_age_sec=10))
    assert any(f.check == "semantic_stats_file" for f in result.failures)

    p = tmp_path / "stats.json"
    p.write_text(json.dumps([1, 2, 3]), encoding="utf-8")
    result = checks.run_checks(target(stats_file=p, stats_updated_max_age_sec=10))
    assert any(f.check == "semantic_stats_file" for f in result.failures)


def test_stats_timestamp_format_branches(tmp_path: Path) -> None:
    p = tmp_path / "stats.json"
    p.write_text(json.dumps({"updated_at": "invalid"}), encoding="utf-8")
    result = checks.run_checks(target(stats_file=p, stats_updated_max_age_sec=10))
    assert any("invalid timestamp format" in f.message for f in result.failures)

    p.write_text(json.dumps({"updated_at": "2026-04-10T10:00:00"}), encoding="utf-8")
    result = checks.run_checks(target(stats_file=p, stats_updated_max_age_sec=10))
    assert any("timezone offset" in f.message for f in result.failures)

    now_z = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    p.write_text(json.dumps({"updated_at": now_z}), encoding="utf-8")
    result = checks.run_checks(target(stats_file=p, stats_updated_max_age_sec=3600))
    assert result.healthy


def test_external_status_internal_state_type_error_branch(tmp_path: Path) -> None:
    now = datetime.now(timezone.utc).isoformat()
    p = tmp_path / "external-status.json"
    p.write_text(
        json.dumps(
            {
                "updated_at": now,
                "internal_state": 1,
                "last_progress_ts": now,
                "last_success_ts": now,
                "reason": {"raw": "ignored"},
                "components": {"pubsub": {"status": "failed"}},
            }
        ),
        encoding="utf-8",
    )
    result = checks.run_checks(
        target(
            external_status_file=p,
            external_status_updated_max_age_sec=60,
            external_status_last_progress_max_age_sec=60,
            external_status_last_success_max_age_sec=60,
        )
    )
    assert any(f.check == "semantic_external_internal_state" for f in result.failures)


def test_stats_last_input_stale_branch(tmp_path: Path) -> None:
    now = datetime.now(timezone.utc)
    p = tmp_path / "stats.json"
    p.write_text(
        json.dumps(
            {
                "updated_at": now.isoformat(),
                "last_input_ts": (now.replace(year=2025)).isoformat(),
                "last_success_ts": now.isoformat(),
            }
        ),
        encoding="utf-8",
    )
    result = checks.run_checks(
        target(
            stats_file=p,
            stats_updated_max_age_sec=3600,
            stats_last_input_max_age_sec=10,
            stats_last_success_max_age_sec=3600,
        )
    )
    assert any(f.check == "semantic_last_input_ts" for f in result.failures)


def test_stats_schema_validates_extended_dependency_fields(tmp_path: Path) -> None:
    now = datetime.now(timezone.utc).isoformat()
    p = tmp_path / "stats.json"
    p.write_text(
        json.dumps(
            {
                "updated_at": now,
                "link_ok": "x",
                "default_route_ok": "x",
                "internet_ip_ok": "x",
                "dns_server_reachable": "x",
                "wan_vs_target_ok": "x",
                "dns_latency_ms": "x",
            }
        ),
        encoding="utf-8",
    )
    result = checks.run_checks(target(stats_file=p, stats_updated_max_age_sec=120))
    names = {f.check for f in result.failures}
    assert "dependency_link" in names
    assert "dependency_default_route" in names
    assert "dependency_internet_ip" in names
    assert "dependency_dns_server" in names
    assert "dependency_wan_target" in names


def test_stats_checks_handles_none_payload(monkeypatch: Any) -> None:
    monkeypatch.setattr(
        "raspi_sentinel.checks.semantic_stats.load_stats",
        lambda path: (None, None),
    )
    failures: list[checks.CheckFailure] = []
    obs: dict[str, Any] = {}
    checks._stats_checks(
        target=target(stats_file=Path("/tmp/unused.json"), stats_updated_max_age_sec=10),
        failures=failures,
        observations=obs,
        now_wall_ts=1_000_000.0,
    )
    assert failures == []


def test_apply_records_progress_check_ignores_missing_records() -> None:
    from raspi_sentinel.state_models import TargetState

    state = TargetState.from_dict(
        {
            "last_records_processed_total": 5,
            "records_stalled_cycles": 2,
            "clock_prev_wall_time_epoch": 1234.5,
        }
    )
    result = checks.CheckResult(target="demo", healthy=True, failures=[], observations={})

    checks.apply_records_progress_check(
        target=target(stats_records_stall_cycles=2),
        target_state=state,
        result=result,
    )

    assert result.failures == []
    assert result.healthy
    assert state.last_records_processed_total == 5
    assert state.records_stalled_cycles == 2
    assert state.clock_prev_wall_time_epoch == 1234.5


def test_apply_records_progress_check_detects_stall_and_preserves_extra_state() -> None:
    from raspi_sentinel.state_models import TargetState

    state = TargetState.from_dict(
        {
            "last_records_processed_total": 10,
            "records_stalled_cycles": 1,
            "clock_prev_wall_time_epoch": 2000.0,
        }
    )
    result = checks.CheckResult(
        target="demo",
        healthy=True,
        failures=[],
        observations={"records_processed_total": 10},
    )

    checks.apply_records_progress_check(
        target=target(stats_records_stall_cycles=2),
        target_state=state,
        result=result,
    )

    assert state.last_records_processed_total == 10
    assert state.records_stalled_cycles == 2
    assert state.clock_prev_wall_time_epoch == 2000.0
    assert any(f.check == "semantic_records_stalled" for f in result.failures)
    assert not result.healthy


def test_apply_records_progress_check_resets_on_counter_drop() -> None:
    from raspi_sentinel.state_models import TargetState

    state = TargetState.from_dict(
        {
            "last_records_processed_total": 10,
            "records_stalled_cycles": 4,
            "clock_prev_monotonic_sec": 333.3,
        }
    )
    result = checks.CheckResult(
        target="demo",
        healthy=True,
        failures=[],
        observations={"records_processed_total": 7},
    )

    checks.apply_records_progress_check(
        target=target(stats_records_stall_cycles=3),
        target_state=state,
        result=result,
    )

    assert state.last_records_processed_total == 7
    assert state.records_stalled_cycles == 0
    assert state.clock_prev_monotonic_sec == 333.3
    assert result.failures == []
    assert result.healthy


def test_apply_records_progress_check_triggers_on_third_stall_cycle() -> None:
    from raspi_sentinel.state_models import TargetState

    state = TargetState.from_dict(
        {
            "last_records_processed_total": 100,
            "records_stalled_cycles": 0,
        }
    )

    for cycle in range(1, 4):
        result = checks.CheckResult(
            target="demo",
            healthy=True,
            failures=[],
            observations={"records_processed_total": 100},
        )
        checks.apply_records_progress_check(
            target=target(stats_records_stall_cycles=3),
            target_state=state,
            result=result,
        )
        if cycle < 3:
            assert result.failures == []
            assert result.healthy
        else:
            assert any(f.check == "semantic_records_stalled" for f in result.failures)
            assert not result.healthy

    assert state.last_records_processed_total == 100
    assert state.records_stalled_cycles == 3
