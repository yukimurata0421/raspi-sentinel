from __future__ import annotations

import logging
import os
import shutil
import stat
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from .config import AppConfig
from .state import ensure_directory

LOG = logging.getLogger(__name__)


@dataclass(slots=True)
class StorageVerifyResult:
    ok: bool
    mount_path: Path
    mount_fs_type: str | None
    owner_uid: int | None
    owner_gid: int | None
    mode: int | None
    free_bytes: int | None
    reason: str | None = None

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "ok": self.ok,
            "mount_path": str(self.mount_path),
            "mount_fs_type": self.mount_fs_type,
            "owner_uid": self.owner_uid,
            "owner_gid": self.owner_gid,
            "mode": f"{self.mode:04o}" if self.mode is not None else None,
            "free_bytes": self.free_bytes,
        }
        if self.reason is not None:
            payload["reason"] = self.reason
        return payload


def _lookup_mount_info(path: Path) -> tuple[Path, str | None]:
    resolved = path.resolve()
    best_mount = Path("/")
    best_type: str | None = None
    try:
        with open("/proc/mounts", "r", encoding="utf-8") as fh:
            for line in fh:
                parts = line.split()
                if len(parts) < 3:
                    continue
                mount_path = Path(parts[1])
                fs_type = parts[2]
                try:
                    mount_resolved = mount_path.resolve()
                except OSError:
                    continue
                if resolved == mount_resolved or resolved.is_relative_to(mount_resolved):
                    if len(str(mount_resolved)) > len(str(best_mount)):
                        best_mount = mount_resolved
                        best_type = fs_type
    except OSError:
        return Path("/"), None
    return best_mount, best_type


def verify_tmpfs_storage(
    *,
    config: AppConfig,
    expected_mode: int | None = 0o755,
    expected_owner_uid: int | None = 0,
    expected_owner_gid: int | None = 0,
) -> StorageVerifyResult:
    if not _is_tmpfs_tiering_enabled(config):
        return StorageVerifyResult(
            ok=True,
            mount_path=config.global_config.state_file.parent,
            mount_fs_type=None,
            owner_uid=None,
            owner_gid=None,
            mode=None,
            free_bytes=None,
            reason="skipped: tmpfs storage tiering is not enabled",
        )

    mount_path = config.global_config.state_file.parent

    desired_mode = expected_mode if expected_mode is not None else 0o755
    if not ensure_directory(mount_path, mode=desired_mode):
        return StorageVerifyResult(
            ok=False,
            mount_path=mount_path,
            mount_fs_type=None,
            owner_uid=None,
            owner_gid=None,
            mode=None,
            free_bytes=None,
            reason=f"failed to prepare mount path directory: {mount_path}",
        )

    if not mount_path.exists():
        return StorageVerifyResult(
            ok=False,
            mount_path=mount_path,
            mount_fs_type=None,
            owner_uid=None,
            owner_gid=None,
            mode=None,
            free_bytes=None,
            reason=f"mount path does not exist: {mount_path}",
        )

    actual_mount, fs_type = _lookup_mount_info(mount_path)
    if actual_mount != mount_path:
        return StorageVerifyResult(
            ok=False,
            mount_path=mount_path,
            mount_fs_type=fs_type,
            owner_uid=None,
            owner_gid=None,
            mode=None,
            free_bytes=None,
            reason=f"path is not an independent mount point: {mount_path} (actual={actual_mount})",
        )
    if config.global_config.storage_require_tmpfs and fs_type != "tmpfs":
        return StorageVerifyResult(
            ok=False,
            mount_path=mount_path,
            mount_fs_type=fs_type,
            owner_uid=None,
            owner_gid=None,
            mode=None,
            free_bytes=None,
            reason=f"mount fs type is not tmpfs: {fs_type}",
        )

    try:
        st = mount_path.stat()
    except OSError as exc:
        return StorageVerifyResult(
            ok=False,
            mount_path=mount_path,
            mount_fs_type=fs_type,
            owner_uid=None,
            owner_gid=None,
            mode=None,
            free_bytes=None,
            reason=f"cannot stat mount path: {exc}",
        )

    mode = stat.S_IMODE(st.st_mode)
    owner_uid = st.st_uid
    owner_gid = st.st_gid
    if expected_mode is not None and mode != expected_mode:
        return StorageVerifyResult(
            ok=False,
            mount_path=mount_path,
            mount_fs_type=fs_type,
            owner_uid=owner_uid,
            owner_gid=owner_gid,
            mode=mode,
            free_bytes=None,
            reason=f"mode mismatch: actual={mode:04o} expected={expected_mode:04o}",
        )
    if expected_owner_uid is not None and owner_uid != expected_owner_uid:
        return StorageVerifyResult(
            ok=False,
            mount_path=mount_path,
            mount_fs_type=fs_type,
            owner_uid=owner_uid,
            owner_gid=owner_gid,
            mode=mode,
            free_bytes=None,
            reason=f"owner uid mismatch: actual={owner_uid} expected={expected_owner_uid}",
        )
    if expected_owner_gid is not None and owner_gid != expected_owner_gid:
        return StorageVerifyResult(
            ok=False,
            mount_path=mount_path,
            mount_fs_type=fs_type,
            owner_uid=owner_uid,
            owner_gid=owner_gid,
            mode=mode,
            free_bytes=None,
            reason=f"owner gid mismatch: actual={owner_gid} expected={expected_owner_gid}",
        )

    write_bytes = config.global_config.storage_verify_write_bytes
    probe_data = b"x" * write_bytes
    probe_fd: int | None = None
    probe_path: Path | None = None
    try:
        probe_fd, probe_name = tempfile.mkstemp(
            dir=mount_path,
            prefix=".tmpfs-write-check-",
            suffix=".bin",
        )
        probe_path = Path(probe_name)
        with os.fdopen(probe_fd, "wb") as fh:
            fh.write(probe_data)
        probe_fd = None
        read_back = probe_path.read_bytes()
    except OSError as exc:
        return StorageVerifyResult(
            ok=False,
            mount_path=mount_path,
            mount_fs_type=fs_type,
            owner_uid=owner_uid,
            owner_gid=owner_gid,
            mode=mode,
            free_bytes=None,
            reason=f"write/read probe failed: {exc}",
        )
    finally:
        if probe_fd is not None:
            try:
                os.close(probe_fd)
            except OSError:
                pass
        try:
            if probe_path is not None:
                probe_path.unlink(missing_ok=True)
        except OSError:
            pass
    if read_back != probe_data:
        return StorageVerifyResult(
            ok=False,
            mount_path=mount_path,
            mount_fs_type=fs_type,
            owner_uid=owner_uid,
            owner_gid=owner_gid,
            mode=mode,
            free_bytes=None,
            reason="write/read probe data mismatch",
        )

    try:
        usage = shutil.disk_usage(mount_path)
    except OSError as exc:
        return StorageVerifyResult(
            ok=False,
            mount_path=mount_path,
            mount_fs_type=fs_type,
            owner_uid=owner_uid,
            owner_gid=owner_gid,
            mode=mode,
            free_bytes=None,
            reason=f"cannot check free bytes: {exc}",
        )
    if usage.free < config.global_config.storage_verify_min_free_bytes:
        return StorageVerifyResult(
            ok=False,
            mount_path=mount_path,
            mount_fs_type=fs_type,
            owner_uid=owner_uid,
            owner_gid=owner_gid,
            mode=mode,
            free_bytes=usage.free,
            reason=(
                "free bytes below threshold: "
                f"actual={usage.free} required={config.global_config.storage_verify_min_free_bytes}"
            ),
        )

    cooldown_sec = config.global_config.storage_verify_cooldown_sec
    if cooldown_sec > 0:
        LOG.info("storage verify cooldown: %ss", cooldown_sec)
        time.sleep(cooldown_sec)

    return StorageVerifyResult(
        ok=True,
        mount_path=mount_path,
        mount_fs_type=fs_type,
        owner_uid=owner_uid,
        owner_gid=owner_gid,
        mode=mode,
        free_bytes=usage.free,
    )


def _is_tmpfs_tiering_enabled(config: AppConfig) -> bool:
    return (
        config.global_config.storage_require_tmpfs
        or config.global_config.state_durable_file is not None
        or bool(config.global_config.state_durable_fields)
    )
