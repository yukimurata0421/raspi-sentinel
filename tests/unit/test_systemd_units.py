from __future__ import annotations

from pathlib import Path


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def test_raspi_sentinel_service_requires_tmpfs_verify_service() -> None:
    service_path = _repo_root() / "systemd" / "raspi-sentinel.service"
    text = service_path.read_text(encoding="utf-8")
    assert "Requires=raspi-sentinel-tmpfs-verify.service" in text
    assert "After=network-online.target raspi-sentinel-tmpfs-verify.service" in text


def test_tmpfs_verify_service_orders_before_raspi_sentinel_service() -> None:
    verify_path = _repo_root() / "systemd" / "raspi-sentinel-tmpfs-verify.service"
    text = verify_path.read_text(encoding="utf-8")
    assert "Before=raspi-sentinel.service" in text
    assert "verify-storage" in text
