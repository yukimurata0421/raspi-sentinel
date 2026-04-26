from __future__ import annotations

import importlib.util
from pathlib import Path

from raspi_sentinel.storage_verify import (
    DEFAULT_VERIFY_EXPECTED_MODE,
    DEFAULT_VERIFY_EXPECTED_OWNER_GID,
    DEFAULT_VERIFY_EXPECTED_OWNER_UID,
)

_SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "install_systemd.py"
_SPEC = importlib.util.spec_from_file_location("install_systemd", _SCRIPT_PATH)
assert _SPEC is not None and _SPEC.loader is not None
install_systemd = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(install_systemd)


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def test_raspi_sentinel_service_requires_tmpfs_verify_service() -> None:
    service_path = _repo_root() / "systemd" / "raspi-sentinel.service"
    text = service_path.read_text(encoding="utf-8")
    assert "Requires=raspi-sentinel-tmpfs-verify.service" in text
    assert "After=network-online.target raspi-sentinel-tmpfs-verify.service" in text
    assert "run-once" in text


def test_render_service_unit_injects_absolute_execstart() -> None:
    source = (_repo_root() / "systemd" / "raspi-sentinel.service").read_text(encoding="utf-8")
    rendered = install_systemd.render_service_unit(
        source,
        raspi_sentinel_bin="/tmp/venv/bin/raspi-sentinel",
        config_path=Path("/etc/raspi-sentinel/config.toml"),
    )
    assert "ExecStart=/tmp/venv/bin/raspi-sentinel" in rendered


def test_tmpfs_verify_service_orders_before_raspi_sentinel_service() -> None:
    verify_path = _repo_root() / "systemd" / "raspi-sentinel-tmpfs-verify.service"
    text = verify_path.read_text(encoding="utf-8")
    assert "Requires=run-raspi\\x2dsentinel.mount" in text
    assert "After=run-raspi\\x2dsentinel.mount" in text
    assert "Before=raspi-sentinel.service" in text
    assert "verify-storage" in text


def test_tmpfs_verify_service_expected_values_match_cli_defaults() -> None:
    verify_path = _repo_root() / "systemd" / "raspi-sentinel-tmpfs-verify.service"
    text = verify_path.read_text(encoding="utf-8")
    assert f"--expected-mode {DEFAULT_VERIFY_EXPECTED_MODE:04o}" in text
    assert f"--expected-owner-uid {DEFAULT_VERIFY_EXPECTED_OWNER_UID}" in text
    assert f"--expected-owner-gid {DEFAULT_VERIFY_EXPECTED_OWNER_GID}" in text


def test_tmpfs_mount_unit_name_matches_mountpoint() -> None:
    mount_path = _repo_root() / "systemd" / "run-raspi\\x2dsentinel.mount"
    text = mount_path.read_text(encoding="utf-8")
    assert "Where=/run/raspi-sentinel" in text
