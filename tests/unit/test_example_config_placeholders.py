from __future__ import annotations

import tomllib
from pathlib import Path

_WEBHOOK_PLACEHOLDER = "https://discord.com/api/webhooks/..."


def _config_paths(repo_root: Path) -> list[Path]:
    return sorted((repo_root / "config").glob("**/*.toml"))


def test_example_configs_require_placeholder_when_discord_notify_enabled() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    config_paths = _config_paths(repo_root)
    assert config_paths, "expected at least one config/*.toml example"

    for config_path in config_paths:
        raw = tomllib.loads(config_path.read_text(encoding="utf-8"))
        notify = raw.get("notify")
        if not isinstance(notify, dict):
            continue
        discord = notify.get("discord")
        if not isinstance(discord, dict):
            continue
        enabled = discord.get("enabled")
        if enabled is not True:
            continue
        assert discord.get("webhook_url") == _WEBHOOK_PLACEHOLDER, (
            f"{config_path.relative_to(repo_root)} must use webhook placeholder "
            f"'{_WEBHOOK_PLACEHOLDER}' when notify.discord.enabled=true"
        )
