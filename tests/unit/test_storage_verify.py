from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

from conftest import make_app_config

from raspi_sentinel.storage_verify import _lookup_mount_info, verify_tmpfs_storage


def test_verify_tmpfs_storage_success(tmp_path: Path, monkeypatch: Any) -> None:
    mount_dir = tmp_path / "run" / "raspi-sentinel"
    mount_dir.mkdir(parents=True)
    cfg = make_app_config(
        global_overrides={
            "state_file": mount_dir / "state.volatile.json",
            "storage_verify_cooldown_sec": 0,
            "storage_verify_write_bytes": 8,
            "storage_verify_min_free_bytes": 1024,
            "storage_require_tmpfs": True,
        }
    )
    monkeypatch.setattr(
        "raspi_sentinel.storage_verify._lookup_mount_info",
        lambda path: (mount_dir, "tmpfs"),
    )
    monkeypatch.setattr(
        "raspi_sentinel.storage_verify.shutil.disk_usage",
        lambda path: SimpleNamespace(total=1024 * 1024, used=512, free=1024 * 1024),
    )
    result = verify_tmpfs_storage(
        config=cfg,
        expected_mode=None,
        expected_owner_uid=None,
        expected_owner_gid=None,
    )
    assert result.ok is True
    assert result.mount_fs_type == "tmpfs"


def test_lookup_mount_info_logs_warning_when_proc_mounts_unreadable(
    monkeypatch: Any, caplog: Any
) -> None:
    def fail_open(*_args: Any, **_kwargs: Any) -> Any:
        raise OSError("cannot read mounts")

    monkeypatch.setattr("builtins.open", fail_open)
    with caplog.at_level("WARNING", logger="raspi_sentinel.storage_verify"):
        mount_path, fs_type = _lookup_mount_info(Path("/run/raspi-sentinel"))

    assert mount_path == Path("/")
    assert fs_type is None
    assert "cannot determine mount fs type" in caplog.text


def test_verify_tmpfs_storage_creates_missing_mount_dir(tmp_path: Path, monkeypatch: Any) -> None:
    mount_dir = tmp_path / "run" / "raspi-sentinel"
    cfg = make_app_config(
        global_overrides={
            "state_file": mount_dir / "state.volatile.json",
            "storage_verify_cooldown_sec": 0,
            "storage_verify_write_bytes": 8,
            "storage_verify_min_free_bytes": 1024,
            "storage_require_tmpfs": True,
        }
    )
    monkeypatch.setattr(
        "raspi_sentinel.storage_verify._lookup_mount_info",
        lambda path: (mount_dir, "tmpfs"),
    )
    monkeypatch.setattr(
        "raspi_sentinel.storage_verify.shutil.disk_usage",
        lambda path: SimpleNamespace(total=1024 * 1024, used=512, free=1024 * 1024),
    )

    assert not mount_dir.exists()
    result = verify_tmpfs_storage(
        config=cfg,
        expected_mode=None,
        expected_owner_uid=None,
        expected_owner_gid=None,
    )

    assert result.ok is True
    assert mount_dir.is_dir()


def test_verify_tmpfs_storage_fails_when_mount_dir_prepare_fails(
    tmp_path: Path, monkeypatch: Any
) -> None:
    mount_dir = tmp_path / "run" / "raspi-sentinel"
    cfg = make_app_config(
        global_overrides={
            "state_file": mount_dir / "state.volatile.json",
            "storage_require_tmpfs": True,
        }
    )
    monkeypatch.setattr(
        "raspi_sentinel.storage_verify.ensure_directory",
        lambda *_args, **_kwargs: False,
    )

    result = verify_tmpfs_storage(config=cfg)

    assert result.ok is False
    assert result.reason is not None
    assert "failed to prepare mount path directory" in result.reason


def test_verify_tmpfs_storage_rejects_non_tmpfs(tmp_path: Path, monkeypatch: Any) -> None:
    mount_dir = tmp_path / "run" / "raspi-sentinel"
    mount_dir.mkdir(parents=True)
    cfg = make_app_config(
        global_overrides={
            "state_file": mount_dir / "state.volatile.json",
            "storage_require_tmpfs": True,
        }
    )
    monkeypatch.setattr(
        "raspi_sentinel.storage_verify._lookup_mount_info",
        lambda path: (mount_dir, "ext4"),
    )
    result = verify_tmpfs_storage(config=cfg)
    assert result.ok is False
    assert result.reason is not None
    assert "not tmpfs" in result.reason


def test_verify_tmpfs_storage_skips_when_tiering_not_enabled(
    tmp_path: Path, monkeypatch: Any
) -> None:
    cfg = make_app_config(
        global_overrides={
            "state_file": tmp_path / "state.json",
            "state_durable_file": None,
            "state_durable_fields": (),
            "storage_require_tmpfs": False,
        }
    )
    result = verify_tmpfs_storage(config=cfg)
    assert result.ok is True
    assert result.reason == "skipped: tmpfs storage tiering is not enabled"


def test_verify_tmpfs_storage_skips_when_tiering_signals_are_absent(
    tmp_path: Path,
) -> None:
    cfg = make_app_config(
        global_overrides={
            "state_file": Path("/run/raspi-sentinel/state.volatile.json"),
            "state_durable_file": None,
            "state_durable_fields": (),
            "storage_require_tmpfs": False,
        }
    )
    result = verify_tmpfs_storage(config=cfg)
    assert result.ok is True
    assert result.reason == "skipped: tmpfs storage tiering is not enabled"


def test_verify_tmpfs_storage_runs_when_durable_tier_is_configured(
    tmp_path: Path, monkeypatch: Any
) -> None:
    mount_dir = tmp_path / "run" / "raspi-sentinel"
    mount_dir.mkdir(parents=True)
    cfg = make_app_config(
        global_overrides={
            "state_file": mount_dir / "state.volatile.json",
            "state_durable_file": tmp_path / "var" / "lib" / "state.durable.json",
            "storage_verify_cooldown_sec": 0,
            "storage_require_tmpfs": False,
        }
    )
    monkeypatch.setattr(
        "raspi_sentinel.storage_verify._lookup_mount_info",
        lambda path: (mount_dir, "ext4"),
    )
    monkeypatch.setattr(
        "raspi_sentinel.storage_verify.shutil.disk_usage",
        lambda path: SimpleNamespace(total=1024 * 1024, used=512, free=1024 * 1024),
    )
    result = verify_tmpfs_storage(
        config=cfg,
        expected_mode=None,
        expected_owner_uid=None,
        expected_owner_gid=None,
    )
    assert result.ok is True
    assert result.mount_fs_type == "ext4"


def test_verify_tmpfs_storage_rejects_low_free_space(tmp_path: Path, monkeypatch: Any) -> None:
    mount_dir = tmp_path / "run" / "raspi-sentinel"
    mount_dir.mkdir(parents=True)
    cfg = make_app_config(
        global_overrides={
            "state_file": mount_dir / "state.volatile.json",
            "storage_verify_cooldown_sec": 0,
            "storage_verify_write_bytes": 8,
            "storage_verify_min_free_bytes": 1024 * 1024,
            "storage_require_tmpfs": True,
        }
    )
    monkeypatch.setattr(
        "raspi_sentinel.storage_verify._lookup_mount_info",
        lambda path: (mount_dir, "tmpfs"),
    )
    monkeypatch.setattr(
        "raspi_sentinel.storage_verify.shutil.disk_usage",
        lambda path: SimpleNamespace(total=4096, used=2048, free=10),
    )
    result = verify_tmpfs_storage(
        config=cfg,
        expected_mode=None,
        expected_owner_uid=None,
        expected_owner_gid=None,
    )
    assert result.ok is False
    assert result.reason is not None
    assert "free bytes below threshold" in result.reason


def test_verify_tmpfs_storage_rejects_non_mount_path(tmp_path: Path, monkeypatch: Any) -> None:
    mount_dir = tmp_path / "run" / "raspi-sentinel"
    mount_dir.mkdir(parents=True)
    cfg = make_app_config(
        global_overrides={
            "state_file": mount_dir / "state.volatile.json",
            "storage_require_tmpfs": True,
        }
    )
    monkeypatch.setattr(
        "raspi_sentinel.storage_verify._lookup_mount_info",
        lambda path: (tmp_path / "run", "tmpfs"),
    )
    result = verify_tmpfs_storage(config=cfg)
    assert result.ok is False
    assert result.reason is not None
    assert "not an independent mount point" in result.reason


def test_verify_tmpfs_storage_rejects_owner_uid_mismatch(tmp_path: Path, monkeypatch: Any) -> None:
    mount_dir = tmp_path / "run" / "raspi-sentinel"
    mount_dir.mkdir(parents=True)
    cfg = make_app_config(
        global_overrides={
            "state_file": mount_dir / "state.volatile.json",
            "storage_verify_cooldown_sec": 0,
            "storage_require_tmpfs": True,
        }
    )
    monkeypatch.setattr(
        "raspi_sentinel.storage_verify._lookup_mount_info",
        lambda path: (mount_dir, "tmpfs"),
    )
    mismatched_uid = mount_dir.stat().st_uid + 1
    result = verify_tmpfs_storage(
        config=cfg,
        expected_mode=None,
        expected_owner_uid=mismatched_uid,
        expected_owner_gid=None,
    )
    assert result.ok is False
    assert result.reason is not None
    assert "owner uid mismatch" in result.reason


def test_verify_tmpfs_storage_rejects_write_probe_failure(tmp_path: Path, monkeypatch: Any) -> None:
    mount_dir = tmp_path / "run" / "raspi-sentinel"
    mount_dir.mkdir(parents=True)
    cfg = make_app_config(
        global_overrides={
            "state_file": mount_dir / "state.volatile.json",
            "storage_verify_cooldown_sec": 0,
            "storage_require_tmpfs": True,
        }
    )
    monkeypatch.setattr(
        "raspi_sentinel.storage_verify._lookup_mount_info",
        lambda path: (mount_dir, "tmpfs"),
    )

    def fail_mkstemp(**_kwargs: Any) -> tuple[int, str]:
        raise OSError("no space left")

    monkeypatch.setattr(
        "raspi_sentinel.storage_verify.tempfile.mkstemp",
        fail_mkstemp,
    )
    result = verify_tmpfs_storage(
        config=cfg,
        expected_mode=None,
        expected_owner_uid=None,
        expected_owner_gid=None,
    )
    assert result.ok is False
    assert result.reason is not None
    assert "write/read probe failed" in result.reason


def test_verify_tmpfs_storage_rejects_write_probe_readback_mismatch(
    tmp_path: Path, monkeypatch: Any
) -> None:
    mount_dir = tmp_path / "run" / "raspi-sentinel"
    mount_dir.mkdir(parents=True)
    cfg = make_app_config(
        global_overrides={
            "state_file": mount_dir / "state.volatile.json",
            "storage_verify_cooldown_sec": 0,
            "storage_require_tmpfs": True,
        }
    )
    monkeypatch.setattr(
        "raspi_sentinel.storage_verify._lookup_mount_info",
        lambda path: (mount_dir, "tmpfs"),
    )
    monkeypatch.setattr(
        "raspi_sentinel.storage_verify.shutil.disk_usage",
        lambda path: SimpleNamespace(total=1024 * 1024, used=512, free=1024 * 1024),
    )
    original_read_bytes = Path.read_bytes

    def read_mismatch(self: Path) -> bytes:
        if self.parent == mount_dir and self.name.startswith(".tmpfs-write-check-"):
            return b"mismatch"
        return original_read_bytes(self)

    monkeypatch.setattr(Path, "read_bytes", read_mismatch)
    result = verify_tmpfs_storage(
        config=cfg,
        expected_mode=None,
        expected_owner_uid=None,
        expected_owner_gid=None,
    )
    assert result.ok is False
    assert result.reason == "write/read probe data mismatch"


def test_verify_tmpfs_storage_calls_cooldown_sleep(tmp_path: Path, monkeypatch: Any) -> None:
    mount_dir = tmp_path / "run" / "raspi-sentinel"
    mount_dir.mkdir(parents=True)
    cfg = make_app_config(
        global_overrides={
            "state_file": mount_dir / "state.volatile.json",
            "storage_verify_cooldown_sec": 3,
            "storage_verify_write_bytes": 8,
            "storage_verify_min_free_bytes": 1024,
            "storage_require_tmpfs": True,
        }
    )
    monkeypatch.setattr(
        "raspi_sentinel.storage_verify._lookup_mount_info",
        lambda path: (mount_dir, "tmpfs"),
    )
    monkeypatch.setattr(
        "raspi_sentinel.storage_verify.shutil.disk_usage",
        lambda path: SimpleNamespace(total=1024 * 1024, used=512, free=1024 * 1024),
    )
    sleep_calls: list[float] = []
    monkeypatch.setattr(
        "raspi_sentinel.storage_verify.time.sleep",
        lambda sec: sleep_calls.append(sec),
    )
    result = verify_tmpfs_storage(
        config=cfg,
        expected_mode=None,
        expected_owner_uid=None,
        expected_owner_gid=None,
    )
    assert result.ok is True
    assert sleep_calls == [3]
