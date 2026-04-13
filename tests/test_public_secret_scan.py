from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def _script() -> Path:
    return Path(__file__).resolve().parents[1] / "tools" / "check_public_secrets.py"


def test_public_secret_scan_rejects_real_discord_webhook(tmp_path: Path) -> None:
    p = tmp_path / "sample.toml"
    p.write_text(
        "[notify.discord]\n"
        "enabled = true\n"
        'webhook_url = "https://discord.com/api/webhooks/123456789012345678/abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMN"\n',
        encoding="utf-8",
    )

    cp = subprocess.run(
        [sys.executable, str(_script()), "--paths", str(p)],
        capture_output=True,
        text=True,
        check=False,
    )

    assert cp.returncode == 1
    assert "discord webhook URL appears to contain a real secret" in cp.stdout


def test_public_secret_scan_allows_placeholder_webhook(tmp_path: Path) -> None:
    p = tmp_path / "sample.toml"
    p.write_text(
        '[notify.discord]\nenabled = true\nwebhook_url = "https://discord.com/api/webhooks/..."\n',
        encoding="utf-8",
    )

    cp = subprocess.run(
        [sys.executable, str(_script()), "--paths", str(p)],
        capture_output=True,
        text=True,
        check=False,
    )

    assert cp.returncode == 0
    assert "[secret-scan] ok" in cp.stdout
