# レビュー対応メモ（2026-04-18）

この文書は、追加レビューで指摘された懸念点（1〜7）への対応状況を記録します。

## 1. `checks` の責務過多（`_probe_network_uplink`）

対応済み。

`src/raspi_sentinel/checks/network_probes.py` を分割し、`probe_network_uplink` はオーケストレーション専用に変更。

- `_probe_link_layer`
- `_probe_route`
- `_probe_gateway`
- `_probe_internet`
- `_probe_dns`
- `_probe_http`

各段は `ProbeResult` を返し、最終的に observation へ反映する構成に統一。

## 2. `TargetConfig.__getattr__` の型安全性低下

部分対応（移行フェーズ開始）。

- 内部コードは `target.deps.* / target.network.* / target.stats.* / target.external.* / target.maintenance.*` 参照へ移行を開始。
- `__getattr__` は deprecation shim として維持。
- shim利用時には `DeprecationWarning` を出す実装を追加（内部モジュールからのアクセスは警告抑制）。

補足:
- 既存テスト互換のため、外部向け後方互換は保持。

## 3. `classify_target_policy` の分岐深度

対応済み（構造化）。

`src/raspi_sentinel/policy.py` を次のグループ関数に再編成。

- `_clock_policy`
- `_external_policy`
- `_network_policy_enabled`
- `_network_policy_disabled`
- `_fallback_policy`

判定優先度は維持しつつ、条件追加時の挿入ポイントを明確化。

## 4. `engine` 主処理の長大化

対応済み。

`src/raspi_sentinel/engine.py` に `_process_single_target` を導入し、
`_evaluate_targets_phase` から target単位の処理責務を抽出。

## 5. `GlobalState.__getitem__/get` の `to_dict()` 乱用

対応済み。

`src/raspi_sentinel/state_models.py` で `__getitem__` / `get` を改善。

- 毎回 `to_dict()` を生成しない
- キー別に必要部分のみ辞書化する実装へ変更

## 6. Delivery backlog/retry ロジック分散

対応済み。

`src/raspi_sentinel/cycle_notifications.py` に `DeliveryBacklogManager` を追加。

集約した責務:

- ネットワーク失敗時 backlog 更新
- summary 送信可否判定
- summary 送信成功/失敗時の状態更新

## 7. `test_checks_internal_branches.py` の monkeypatch 過多

部分対応。

- 直接的なテスト全面書き換えは未実施。
- ただし network probe の関数分割により、今後は粒度の小さい単体テストへ移行しやすい構造になった。

## 検証

対応後に以下を実施し、すべて成功。

- `ruff check src tests`
- `mypy`
- `pytest -q`

