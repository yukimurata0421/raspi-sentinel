# レビュー対応メモ（2026-04-26）

対象:

- レビュー1: 「コードレビュー（raspi-sentinel）」(A/B/C/D/E)
- レビュー2: 「文章とコードが合っていない点」(P0/P1/P2)

この文書は、上記2件のレビュー項目について 2026-04-26 時点の対応状況を記録する。

## 1. 対応済み（実装 + テスト）

### 1-1. P0/P1/P2 の整合・運用導線

- P0: Quickstart install と systemd `ExecStart` の不整合
  - 対応: `systemd/*.service` を `ExecStart=raspi-sentinel ...` に統一。
  - 対応: `scripts/install_systemd.py` で実行バイナリ自動検出 + `--raspi-sentinel-bin` 上書き + `--config-path` 反映。
  - 検証: `tests/unit/test_install_systemd.py` 追加。
- P0: failure injection パスと example config の不整合
  - 対応: `config/raspi-sentinel.beta-demo.toml` 新設。
  - 対応: README/README.ja の quickstart を demo config と `/tmp/raspi-sentinel-demo/heartbeat.txt` に統一。
- P1: command failure message の secret redaction
  - 対応: `checks/command_checks.py` で command/snippet を redaction 通過後に出力。
  - 対応: `redaction.py` へ Discord webhook path token の masking を追加。
  - 検証: `tests/unit/test_redaction.py` 更新。
- P1: `--dry-run` 時の通知挙動
  - 対応: 既定で通知抑止。`--send-notifications` 明示時のみ dry-run 通知許可。
  - 実装: `cli.py` / `engine.py`。
  - 検証: `tests/scenario/test_engine_integration.py` へ回帰テスト追加。
- P1: `reboot_threshold == restart_threshold` 許容
  - 対応: global/target とも `reboot_threshold > restart_threshold` を必須化。
  - 実装: `config_loader.py` / `config_summary.py`。
  - 検証: `tests/unit/test_config_validation*.py`, `tests/unit/test_config_summary.py` 更新。
- P2: `doctor --fix-permissions` の before/after 混在
  - 対応: 権限修正を先に実行し、その後 doctor snapshot を構築。
  - 実装: `cli.py`。
  - 検証: `tests/e2e/test_cli_behavior.py` 更新。
- P2: target name/services 正規化不足
  - 対応: target name/service 名を `strip()` 正規化。空文字 service を reject。
  - 実装: `config_loader.py`。
  - 検証: `tests/unit/test_config_validation_branches.py` 更新。

### 1-2. ロジックレビュー A/B 項目（優先度高）

- A-1: `test_public_secret_scan.py` の repo root 解決
  - 対応: `parents[2]` に修正済み（既対応を再確認）。
- A-2: reboot window/cooldown の境界条件意図が不明
  - 対応: `recovery._can_reboot` に境界意図コメント追加（window は `<=`、cooldown は `<`）。
  - 対応: `docs/principles/engineering-decisions.md` に方針追記。
- A-3: `_quarantine_corrupt_state` 上限意図が読み取りづらい
  - 対応: `state.py` に `_QUARANTINE_MAX_SUFFIX = 99` 定数を導入しログに上限表示。
- A-4: `append_event` の mkdir タイミング
  - 対応: `status_events.append_event` で `mkdir` を rotate 前へ移動。
- A-5: reboot intent 後 persist 失敗時の可観測性
  - 対応: cycle report `reason` に `state_persist_failed_after_reboot_intent` を追加。
  - 検証: `tests/scenario/test_engine_integration.py` へ回帰テスト追加。
- A-6: `safe_int(..., 0) or 0` 冗長
  - 対応: `time_health._update_network_counters` を整理。
- A-7: dependency observation から failure 化する意図が読みにくい
  - 対応: `checks/runner.py` に説明コメント追加。
- A-9: `_probe_route` で iface match 後に fallback route が上書き
  - 対応: `checks/network_probes.py` 修正。
  - 検証: `tests/unit/test_checks_internal_network.py` に mixed default route の回帰テスト追加。
- B-1: reboot request 後に target 評価を打ち切る意図が未明記
  - 対応: `engine.py` に意図コメント追加。
  - 検証: `tests/scenario/test_engine_integration.py` で打ち切り挙動を固定。
- B-3: `_reset_deprecated_attr_warnings_for_tests` の用途明示
  - 対応: `config_models.py` docstring を test-only に明示。
- B-4: `run_cycle_collect` の例外ハンドリング
  - 対応: `TimeoutError` と `OSError` を分離。

## 2. 追加実装（全項目対応）

前回「提案として受理・未実装」に分離していた項目は、今回すべて実装済み。

- B-2: `apply_recovery` を責務分割（失敗記録、clock-reboot、threshold判定）。
- B-5: observation/evidence field 定義を `checks/models.py` に集約し、
  `status_events.py` / `monitor_stats.py` で共有。
- B-6: `_send_with_tracking` の共通引数を `NotificationContext` dataclass に集約。
- B-7: `StorageVerifyResult` の optional field に default を設定し、戻り値初期化を簡略化。
- B-8: `PROCESS_CHECK_NAMES` を `policy.py` ローカル定義から `checks/models.py` へ移動。
- B-9: `TargetReport.evidence` 型を `ObservationMap` に変更。
- C-1: restart timeout を config 化（`global.restart_service_timeout_sec`、default=30）。
- C-2: maintenance 空コマンド時の warning 追加。
- C-3: notify retry backoff を config 化（`notify.discord.retry_backoff_base_sec`、default=0.5）。
- C-4: `diagnostics._read_os_release` を `shlex` ベースに強化。
- C-5: `deploy_pi5_guard.py` の rsync exclude を拡張
  (`.coverage`, `coverage.xml`, `dist/`, `build/`, `*.egg-info/`)。

## 3. 実行結果（今回確認）

- `bash scripts/prepush_check.sh`: pass
- `PYTHONPATH=src pytest -q`: pass（全件）
