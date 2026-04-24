from __future__ import annotations

import json
from pathlib import Path

from raspi_sentinel.contracts import (
    ALLOWED_TARGET_STATUS,
    STATE_SCHEMA_VERSION,
    STATS_SCHEMA_VERSION,
)


def _load_json(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def test_stats_contract_fixture_matches_schema_version_and_status_enum() -> None:
    fixture = _load_json(Path("tests/fixtures/contracts/stats_v1.json"))
    assert fixture["stats_schema_version"] == STATS_SCHEMA_VERSION
    assert fixture["status"] in ALLOWED_TARGET_STATUS
    assert isinstance(fixture["targets"], dict)
    for payload in fixture["targets"].values():
        assert payload["status"] in ALLOWED_TARGET_STATUS
        assert isinstance(payload["reason"], str)


def test_state_contract_fixture_matches_schema_version_and_required_top_level_keys() -> None:
    fixture = _load_json(Path("tests/fixtures/contracts/state_v1.json"))
    assert fixture["state_schema_version"] == STATE_SCHEMA_VERSION
    for key in ("targets", "reboots", "followups", "notify", "monitor_stats"):
        assert key in fixture


def test_contract_schema_files_exist_and_define_required_version_fields() -> None:
    stats_schema = _load_json(Path("docs/schemas/stats.schema.json"))
    state_schema = _load_json(Path("docs/schemas/state.schema.json"))
    assert "stats_schema_version" in stats_schema.get("required", [])
    assert "state_schema_version" in state_schema.get("required", [])
