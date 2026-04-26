from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

_BLOCK_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    (
        "discord_webhook",
        re.compile(r"https://discord\.com/api/webhooks/\d{6,}/[A-Za-z0-9_-]{20,}"),
    ),
    ("github_pat", re.compile(r"ghp_[A-Za-z0-9]{36}")),
    ("github_pat_v2", re.compile(r"github_pat_[A-Za-z0-9_]{80,}")),
    ("slack_token", re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}")),
    ("aws_access_key_id", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("google_api_key", re.compile(r"\bAIza[0-9A-Za-z\-_]{35}\b")),
]

_EXAMPLE_CONFIG_PATH = Path("config/raspi-sentinel.example.toml")
_EXAMPLE_DISCORD_WEBHOOK_PATTERN = re.compile(
    r'webhook_url\s*=\s*"https://discord\.com/api/webhooks/(?!\.\.\.)[^"]+"'
)


def _tracked_files(repo_root: Path) -> list[Path]:
    try:
        out = subprocess.check_output(
            ["git", "ls-files"],
            cwd=repo_root,
            text=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        pytest.skip("git ls-files unavailable in this environment")
    files: list[Path] = []
    for rel in out.splitlines():
        rel = rel.strip()
        if not rel:
            continue
        files.append(repo_root / rel)
    return files


def test_no_accidental_secrets_in_tracked_files() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    offenders: list[str] = []
    for path in _tracked_files(repo_root):
        if not path.exists() or path.is_dir():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for label, pattern in _BLOCK_PATTERNS:
            match = pattern.search(text)
            if match is None:
                continue
            offenders.append(f"{path.relative_to(repo_root)}:{label}:{match.group(0)[:80]}")
            break

    assert offenders == [], "Potential secret-like values found:\n" + "\n".join(offenders)


def test_example_config_discord_webhook_must_be_placeholder() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    path = repo_root / _EXAMPLE_CONFIG_PATH
    text = path.read_text(encoding="utf-8")
    assert _EXAMPLE_DISCORD_WEBHOOK_PATTERN.search(text) is None, (
        "config/raspi-sentinel.example.toml must not include a real Discord webhook URL; "
        "use https://discord.com/api/webhooks/..."
    )
