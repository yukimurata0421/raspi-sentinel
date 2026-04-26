from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from raspi_sentinel.diagnostics import _load_last_run_status


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
