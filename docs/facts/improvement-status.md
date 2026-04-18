# 改善提案対応状況

作成日: 2026-04-17

この文書は、提示された改善提案（1〜10）に対する `raspi-sentinel` の実装状況を整理したものです。

## 1. `checks.py` の分割（優先度: 高）

対応済み。

- 旧: `src/raspi_sentinel/checks.py`（単一ファイル）
- 新: `src/raspi_sentinel/checks/` パッケージへ分割
  - `__init__.py`
  - `models.py`
  - `file_checks.py`
  - `command_checks.py`
  - `semantic_stats.py`
  - `network_probes.py`
  - `runner.py`

`checks/__init__.py` から `run_checks` / `apply_records_progress_check` を公開。

## 2. Observations の dataclass/TypedDict 化

対応強化（2026-04-18）。

- `checks/models.py` に `Observations` (`TypedDict`) を追加。
- `policy.py` では型付きビューとして扱うように更新。
- さらに `checks/models.py` に `ObservationBooleanFlag` (`Literal`) と
  `is_observation_flag_true()` を追加し、ポリシー側の latency/loss 系の
  observation 参照を型付きヘルパー経由に置換。

補足:
- 現時点では `CheckResult.observations` 本体は `ObservationMap` を維持（既存互換を優先）。
- 完全な `CheckResult.observations: Observations` への移行は、`time_health.py` や
  `status_events.py` 側のキー定義整理を含む段階的対応が必要。

## 11. `config.py` 肥大化（34KB級）の分割

対応済み（2026-04-18）。

- 旧: `src/raspi_sentinel/config.py`（単一巨大モジュール）
- 新:
  - `src/raspi_sentinel/config_models.py`
  - `src/raspi_sentinel/config_loader.py`
  - `src/raspi_sentinel/config.py`（公開API互換のファサード）

補足:
- 既存互換のため `raspi_sentinel.config` から `load_config` と補助関数
  (`_require_int`, `_validate_target_rules`, `_warn_config_permissions`) は引き続き参照可能。

## 12. `test_checks_internal_branches.py` の巨大化

対応済み（2026-04-18）。

- 旧: `tests/test_checks_internal_branches.py`（34KB級）
- 新:
  - `tests/test_checks_internal_file_command.py`
  - `tests/test_checks_internal_network.py`
  - `tests/test_checks_internal_stats_and_progress.py`
  - `tests/checks_internal_branches_helpers.py`（共通ヘルパー）

## 3. `_probe_network_uplink` の低レベルソケット実装

対応済み（HTTP部分）。

- HTTPプローブを低レベル socket 直実装から `urllib.request` ベースへ変更。
- エラー分類 (`dns_resolution_failed`, `connect_timeout`, `read_timeout`, `tls_error`, `connection_refused`, `non_2xx`, `unknown`) は維持。

補足:
- 依存追加はなし（`dependencies = []` 方針を維持）。

## 4. `engine.py` の `_run_cycle_collect_locked` 分割

対応済み。

主な分割先:
- `_evaluate_targets_phase`
- `_run_notification_phase`
- `_build_cycle_report`

加えて、レポート構造を `TypedDict` 化（`TargetReport`, `CycleReport`）。

## 5. `TargetConfig` のフィールド肥大化（中長期）

未対応（設計判断として保留）。

理由:
- `checks: list[CheckSpec]` 型への移行は設定フォーマットの breaking change を伴う。
- v1.0 マイルストンでの実施が妥当。

## 6. exit code 一覧表

対応済み。

- 実装定数: `src/raspi_sentinel/exit_codes.py`
- ドキュメント: `docs/facts/exit-codes.md`

## 7. `_service_active_check` timeout のハードコード

対応済み。

- タイムアウト既定値を設定ロード時に集約。
- `command_timeout_sec` / `dependency_check_timeout_sec` はグローバル既定値から補完。

## 8. structured logging オプション

対応済み。

- `logging_utils.py` に JSON ログフォーマッタを追加。
- CLI オプション `--structured-logging` を追加。

## 9. `README.ja.md`

対応済み。

- `README.ja.md` を追加。
- `README.md` からリンク。

## 10. CHANGELOG の運用（Unreleased維持）

対応済み。

- `CHANGELOG.md` の `Unreleased` セクションを更新。
- 今回のリファクタと文書追加を反映。

## 検証結果

最新の反映後に以下を実行し、すべて成功。

- `ruff check src tests`
- `mypy`
- `pytest -q`
