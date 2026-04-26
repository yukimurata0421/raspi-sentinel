from __future__ import annotations

import json
import logging
import os
from pathlib import Path

import pytest

from raspi_sentinel.diagnostics import (
    _config_permission_status,
    _load_last_run_status,
    fix_config_permissions,
)


def test_load_last_run_status_warns_on_future_schema_version(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    stats_path = tmp_path / "stats.json"
    stats_path.write_text(
        json.dumps(
            {
                "stats_schema_version": 999,
                "status": "ok",
            }
        ),
        encoding="utf-8",
    )
    with caplog.at_level(logging.WARNING):
        status, schema_version = _load_last_run_status(stats_path)
    assert status == "ok"
    assert schema_version == 999
    assert "schema version is newer than supported" in caplog.text


def test_load_last_run_status_warns_on_unknown_status(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    stats_path = tmp_path / "stats.json"
    stats_path.write_text(
        json.dumps(
            {
                "stats_schema_version": 1,
                "status": "flapping",
            }
        ),
        encoding="utf-8",
    )
    with caplog.at_level(logging.WARNING):
        status, schema_version = _load_last_run_status(stats_path)
    assert status == "unknown"
    assert schema_version == 1
    assert "unknown status value" in caplog.text


def test_config_permission_status_warns_on_group_readable(tmp_path: Path) -> None:
    conf = tmp_path / "config.toml"
    conf.write_text("x", encoding="utf-8")
    conf.chmod(0o640)
    status, detail = _config_permission_status(conf)
    assert status == "warn"
    assert detail is not None and "group/world readable" in detail


def test_fix_config_permissions_dry_run_no_change(tmp_path: Path) -> None:
    conf = tmp_path / "config.toml"
    conf.write_text("x", encoding="utf-8")
    conf.chmod(0o644)
    result = fix_config_permissions(config_path=conf, dry_run=True)
    assert result["status"] == "dry-run"
    mode = os.stat(conf).st_mode & 0o777
    assert mode == 0o644
