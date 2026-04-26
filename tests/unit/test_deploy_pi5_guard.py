from __future__ import annotations

import importlib.util
from datetime import datetime
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest


def _load_module() -> ModuleType:
    repo_root = Path(__file__).resolve().parents[2]
    script_path = repo_root / "scripts" / "deploy_pi5_guard.py"
    spec = importlib.util.spec_from_file_location("deploy_pi5_guard", script_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("failed to load deploy_pi5_guard module")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def deploy_mod() -> ModuleType:
    return _load_module()


def test_extract_json_object_from_mixed_output(deploy_mod: ModuleType) -> None:
    payload = deploy_mod._extract_json_object('line1\n{"overall_status":"ok"}\nline3')
    assert payload == {"overall_status": "ok"}


def test_extract_json_object_raises_when_json_missing(deploy_mod: ModuleType) -> None:
    with pytest.raises(deploy_mod.DeployError, match="failed to find JSON object"):
        deploy_mod._extract_json_object("no json here")


def test_run_returns_completed_process_without_exec_in_dry_run(
    deploy_mod: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail_if_called(*args: Any, **kwargs: Any) -> Any:
        del args
        del kwargs
        raise AssertionError("subprocess.run should not be called in dry-run")

    monkeypatch.setattr(deploy_mod.subprocess, "run", fail_if_called)
    result = deploy_mod._run(["echo", "hello"], dry_run=True)
    assert result.returncode == 0


def test_require_ok_raises_with_command_detail(deploy_mod: ModuleType) -> None:
    result = deploy_mod.subprocess.CompletedProcess(["cmd"], 1, "", "boom")
    with pytest.raises(deploy_mod.DeployError, match="probe failed: hostname"):
        deploy_mod._require_ok(result, what="probe", command="hostname", dry_run=False)


def test_preflight_raises_when_check_fails(
    deploy_mod: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    call_count = 0

    def fake_run_ssh(host: str, remote_cmd: str, *, dry_run: bool) -> Any:
        del host
        del remote_cmd
        del dry_run
        nonlocal call_count
        call_count += 1
        if call_count == 2:
            return deploy_mod.subprocess.CompletedProcess(["ssh"], 1, "", "sudo denied")
        return deploy_mod.subprocess.CompletedProcess(["ssh"], 0, "", "")

    monkeypatch.setattr(deploy_mod, "_run_ssh", fake_run_ssh)
    with pytest.raises(deploy_mod.DeployError, match="sudo non-interactive failed"):
        deploy_mod._preflight("pi5-guard@pi5-guard", dry_run=False)


def test_run_stage_validation_rejects_unexpected_status(
    deploy_mod: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []

    def fake_run_ssh(host: str, remote_cmd: str, *, dry_run: bool) -> Any:
        del host
        del dry_run
        calls.append(remote_cmd)
        if len(calls) == 1:
            return deploy_mod.subprocess.CompletedProcess(["ssh"], 0, "", "")
        return deploy_mod.subprocess.CompletedProcess(
            ["ssh"],
            0,
            '{"overall_status":"flapping"}',
            "",
        )

    monkeypatch.setattr(deploy_mod, "_run_ssh", fake_run_ssh)

    with pytest.raises(deploy_mod.DeployError, match="unexpected staging dry-run payload"):
        deploy_mod._run_stage_validation("pi5-guard@pi5-guard", "/tmp/stage", dry_run=False)


def test_post_deploy_health_gate_rejects_missing_state_persisted(
    deploy_mod: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    outputs = iter(
        (
            deploy_mod.subprocess.CompletedProcess(["ssh"], 0, "", ""),
            deploy_mod.subprocess.CompletedProcess(
                ["ssh"],
                0,
                '{"overall_status":"ok","state_persisted":true}',
                "",
            ),
            deploy_mod.subprocess.CompletedProcess(
                ["ssh"],
                0,
                '{"overall_status":"ok","state_persisted":false}',
                "",
            ),
        )
    )

    def fake_run_ssh(host: str, remote_cmd: str, *, dry_run: bool) -> Any:
        del host
        del remote_cmd
        del dry_run
        return next(outputs)

    monkeypatch.setattr(deploy_mod, "_run_ssh", fake_run_ssh)

    with pytest.raises(deploy_mod.DeployError, match="state_persisted is not true"):
        deploy_mod._post_deploy_health_gate("pi5-guard@pi5-guard", dry_run=False)


def test_rollback_raises_when_backup_missing(
    deploy_mod: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        deploy_mod,
        "_run_ssh",
        lambda host, remote_cmd, *, dry_run: deploy_mod.subprocess.CompletedProcess(
            ["ssh"], 1, "", "not found"
        ),
    )
    with pytest.raises(deploy_mod.DeployError, match="rollback failed"):
        deploy_mod._rollback("pi5-guard@pi5-guard", "/opt/missing", dry_run=False)


def test_switch_release_excludes_venv_from_delete(
    deploy_mod: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen_cmd: list[str] = []

    def fake_run_ssh(host: str, remote_cmd: str, *, dry_run: bool) -> Any:
        del host
        del dry_run
        seen_cmd.append(remote_cmd)
        return deploy_mod.subprocess.CompletedProcess(["ssh"], 0, "", "")

    monkeypatch.setattr(deploy_mod, "_run_ssh", fake_run_ssh)
    backup_dir = deploy_mod._switch_release(
        "pi5-guard@pi5-guard",
        "/tmp/stage",
        release_id="20260426T111111",
        dry_run=False,
    )
    assert backup_dir == "/opt/raspi-sentinel.rollback.20260426T111111"
    assert seen_cmd
    assert "--exclude .venv/" in seen_cmd[0]


def test_main_safe_mode_runs_stage_validation(
    deploy_mod: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[str, str]] = []

    class _FixedDatetime:
        @classmethod
        def now(cls) -> datetime:
            return datetime(2026, 4, 26, 12, 34, 56)

    def fake_preflight(host: str, *, dry_run: bool) -> None:
        calls.append(("preflight", host))
        assert dry_run is True

    def fake_run_ssh(host: str, remote_cmd: str, *, dry_run: bool) -> Any:
        calls.append(("ssh", remote_cmd))
        assert host == "pi5-guard@pi5-guard"
        assert dry_run is True
        return deploy_mod.subprocess.CompletedProcess(["ssh"], 0, "", "")

    def fake_rsync(local_root: Path, host: str, stage_dir: str, *, dry_run: bool) -> None:
        del local_root
        assert host == "pi5-guard@pi5-guard"
        assert stage_dir.endswith("raspi-sentinel-20260426T123456")
        assert dry_run is True
        calls.append(("rsync", stage_dir))

    def fake_stage_validation(host: str, stage_dir: str, *, dry_run: bool) -> None:
        assert host == "pi5-guard@pi5-guard"
        assert stage_dir.endswith("raspi-sentinel-20260426T123456")
        assert dry_run is True
        calls.append(("stage_validation", stage_dir))

    def fake_switch(host: str, stage_dir: str, *, release_id: str, dry_run: bool) -> str:
        assert host == "pi5-guard@pi5-guard"
        assert stage_dir.endswith("raspi-sentinel-20260426T123456")
        assert release_id == "20260426T123456"
        assert dry_run is True
        calls.append(("switch", stage_dir))
        return "/opt/backup"

    def fake_post(host: str, *, dry_run: bool) -> None:
        assert host == "pi5-guard@pi5-guard"
        assert dry_run is True
        calls.append(("post", host))

    monkeypatch.setattr(deploy_mod, "datetime", _FixedDatetime)
    monkeypatch.setattr(deploy_mod, "_preflight", fake_preflight)
    monkeypatch.setattr(deploy_mod, "_run_ssh", fake_run_ssh)
    monkeypatch.setattr(deploy_mod, "_rsync_to_stage", fake_rsync)
    monkeypatch.setattr(deploy_mod, "_run_stage_validation", fake_stage_validation)
    monkeypatch.setattr(deploy_mod, "_switch_release", fake_switch)
    monkeypatch.setattr(deploy_mod, "_post_deploy_health_gate", fake_post)

    rc = deploy_mod.main(["--dry-run"])
    assert rc == 0
    assert any(name == "stage_validation" for name, _ in calls)


def test_main_fast_mode_skips_stage_validation(
    deploy_mod: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    stage_validation_called = False

    class _FixedDatetime:
        @classmethod
        def now(cls) -> datetime:
            return datetime(2026, 4, 26, 9, 0, 0)

    def fake_run_ssh(host: str, remote_cmd: str, *, dry_run: bool) -> Any:
        del host
        del remote_cmd
        del dry_run
        return deploy_mod.subprocess.CompletedProcess(["ssh"], 0, "", "")

    def fake_stage_validation(host: str, stage_dir: str, *, dry_run: bool) -> None:
        del host
        del stage_dir
        del dry_run
        nonlocal stage_validation_called
        stage_validation_called = True

    monkeypatch.setattr(deploy_mod, "datetime", _FixedDatetime)
    monkeypatch.setattr(deploy_mod, "_preflight", lambda host, *, dry_run: None)
    monkeypatch.setattr(deploy_mod, "_run_ssh", fake_run_ssh)
    monkeypatch.setattr(
        deploy_mod, "_rsync_to_stage", lambda local_root, host, stage_dir, *, dry_run: None
    )
    monkeypatch.setattr(deploy_mod, "_run_stage_validation", fake_stage_validation)
    monkeypatch.setattr(
        deploy_mod,
        "_switch_release",
        lambda host, stage_dir, *, release_id, dry_run: "/opt/backup",
    )
    monkeypatch.setattr(deploy_mod, "_post_deploy_health_gate", lambda host, *, dry_run: None)

    rc = deploy_mod.main(["--mode", "fast", "--dry-run"])
    assert rc == 0
    assert stage_validation_called is False


def test_main_rolls_back_when_post_deploy_fails(
    deploy_mod: ModuleType, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    rollback_calls: list[str] = []

    class _FixedDatetime:
        @classmethod
        def now(cls) -> datetime:
            return datetime(2026, 4, 26, 13, 0, 0)

    monkeypatch.setattr(deploy_mod, "datetime", _FixedDatetime)
    monkeypatch.setattr(deploy_mod, "_preflight", lambda host, *, dry_run: None)
    monkeypatch.setattr(
        deploy_mod,
        "_run_ssh",
        lambda host, remote_cmd, *, dry_run: deploy_mod.subprocess.CompletedProcess(
            ["ssh"], 0, "", ""
        ),
    )
    monkeypatch.setattr(
        deploy_mod, "_rsync_to_stage", lambda local_root, host, stage_dir, *, dry_run: None
    )
    monkeypatch.setattr(
        deploy_mod,
        "_run_stage_validation",
        lambda host, stage_dir, *, dry_run: None,
    )
    monkeypatch.setattr(
        deploy_mod,
        "_switch_release",
        lambda host, stage_dir, *, release_id, dry_run: "/opt/raspi-sentinel.rollback.20260426",
    )
    monkeypatch.setattr(
        deploy_mod,
        "_post_deploy_health_gate",
        lambda host, *, dry_run: (_ for _ in ()).throw(deploy_mod.DeployError("post failed")),
    )

    def fake_rollback(host: str, backup_dir: str, *, dry_run: bool) -> None:
        del host
        del dry_run
        rollback_calls.append(backup_dir)

    monkeypatch.setattr(deploy_mod, "_rollback", fake_rollback)

    rc = deploy_mod.main(["--dry-run"])
    captured = capsys.readouterr()
    assert rc == 1
    assert rollback_calls == ["/opt/raspi-sentinel.rollback.20260426"]
    assert "deploy failed: post failed" in captured.err
    assert "rollback completed from /opt/raspi-sentinel.rollback.20260426" in captured.err
