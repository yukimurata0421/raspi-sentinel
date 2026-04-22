from __future__ import annotations

from pathlib import Path


def test_example_config_webhook_url_is_placeholder_literal() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    config_path = repo_root / "config" / "raspi-sentinel.example.toml"
    text = config_path.read_text(encoding="utf-8")
    assert 'webhook_url = "https://discord.com/api/webhooks/..."' in text
