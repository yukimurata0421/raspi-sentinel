from __future__ import annotations

import importlib.util
import os
import time
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT_PATH = _REPO_ROOT / "scripts" / "failure_inject.py"
_SPEC = importlib.util.spec_from_file_location("failure_inject", _SCRIPT_PATH)
assert _SPEC is not None and _SPEC.loader is not None
failure_inject = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(failure_inject)


def test_fresh_file_creates_and_updates_mtime(tmp_path: Path) -> None:
    target = tmp_path / "demo" / "heartbeat.txt"
    rc = failure_inject.main(["fresh-file", "--path", str(target)])
    assert rc == 0
    assert target.exists()
    now = time.time()
    assert abs(os.path.getmtime(target) - now) < 3.0


def test_fresh_file_dry_run_does_not_create_file(tmp_path: Path) -> None:
    target = tmp_path / "demo" / "heartbeat.txt"
    rc = failure_inject.main(["--dry-run", "fresh-file", "--path", str(target)])
    assert rc == 0
    assert not target.exists()
