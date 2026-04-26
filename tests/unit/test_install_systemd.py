from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT_PATH = _REPO_ROOT / "scripts" / "install_systemd.py"
_SPEC = importlib.util.spec_from_file_location("install_systemd", _SCRIPT_PATH)
assert _SPEC is not None and _SPEC.loader is not None
install_systemd = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(install_systemd)


def _write(path: Path, text: str) -> None:
    path.write_text(text.strip() + "\n", encoding="utf-8")


def test_main_renders_execstart_with_detected_binary(tmp_path: Path, monkeypatch: Any) -> None:
    src_dir = tmp_path / "systemd"
    dst_dir = tmp_path / "dest"
    src_dir.mkdir()
    dst_dir.mkdir()
    _write(
        src_dir / "raspi-sentinel.service",
        """
        [Service]
        ExecStart=raspi-sentinel -c /etc/raspi-sentinel/config.toml run-once
        """,
    )
    _write(
        src_dir / "raspi-sentinel-tmpfs-verify.service",
        """
        [Service]
        ExecStart=raspi-sentinel -c /etc/raspi-sentinel/config.toml verify-storage
        """,
    )
    _write(src_dir / "raspi-sentinel.timer", "[Timer]\nOnUnitActiveSec=30s")

    monkeypatch.setattr(install_systemd.shutil, "which", lambda name: "/tmp/bin/raspi-sentinel")
    monkeypatch.setattr(install_systemd, "_run", lambda cmd, dry_run: None)
    rc = install_systemd.main(
        [
            "--source-dir",
            str(src_dir),
            "--dest-dir",
            str(dst_dir),
        ]
    )
    assert rc == 0
    service_text = (dst_dir / "raspi-sentinel.service").read_text(encoding="utf-8")
    verify_text = (dst_dir / "raspi-sentinel-tmpfs-verify.service").read_text(encoding="utf-8")
    assert (
        "ExecStart=/tmp/bin/raspi-sentinel -c /etc/raspi-sentinel/config.toml run-once"
        in service_text
    )
    assert (
        "/tmp/bin/raspi-sentinel -c /etc/raspi-sentinel/config.toml verify-storage" in verify_text
    )


def test_main_raises_when_binary_not_found(tmp_path: Path, monkeypatch: Any) -> None:
    src_dir = tmp_path / "systemd"
    dst_dir = tmp_path / "dest"
    src_dir.mkdir()
    dst_dir.mkdir()
    _write(src_dir / "raspi-sentinel.service", "[Service]\nExecStart=raspi-sentinel run-once")
    _write(
        src_dir / "raspi-sentinel-tmpfs-verify.service",
        "[Service]\nExecStart=raspi-sentinel verify-storage",
    )
    _write(src_dir / "raspi-sentinel.timer", "[Timer]\nOnUnitActiveSec=30s")

    monkeypatch.setattr(install_systemd.shutil, "which", lambda name: None)
    with pytest.raises(FileNotFoundError, match="could not resolve raspi-sentinel executable"):
        install_systemd.main(
            [
                "--source-dir",
                str(src_dir),
                "--dest-dir",
                str(dst_dir),
            ]
        )
