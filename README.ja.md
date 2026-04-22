# raspi-sentinel (日本語ガイド)

`raspi-sentinel` は Raspberry Pi 上の `systemd` 管理サービスを監視し、
`warn -> restart -> reboot` の段階的リカバリを行う小さな監視/復旧レイヤーです。

## 責務境界

この節では `raspi-sentinel` の責務と、責務外（任意連携）の境界を示します。

### コア（本プロジェクト）

- OS が生きている前提での論理監視と段階的復旧（warn -> restart -> reboot）
- 外部ステータス JSON の浅い監視（汎用契約ベース）

### 任意連携（責務外）

- ハードウェア watchdog などの下位レイヤーフェイルセーフ
- コアの実行ロジックとは分離された運用連携

## できること

- ファイル更新時刻監視（heartbeat / output）
- コマンド監視、サービス稼働監視
- `stats.json` / 外部ステータス JSON の鮮度・状態監視
- ネットワーク uplink 監視（link / route / gateway / WAN / DNS / HTTP）
- 連続失敗回数とクールダウンを使った安全な復旧判断
- Discord 通知（再送キュー集約あり）

## Health Topology スナップショット

`stats.json` をもとに描画したヘルストポロジーの表示例です。

![raspi-sentinel Health Topology](docs/images/health-topology.png)

## Storage Tiers（SD寿命最適化・任意）

高頻度書き込みを tmpfs に逃がしつつ、再起動ガードに必要な状態を永続化するオプションを提供します。

- 詳細: [docs/storage-tiers.md](docs/storage-tiers.md)
- 設定例: `config/raspi-sentinel.example.toml` の `[storage]`
- 検証コマンド: `raspi-sentinel -c /etc/raspi-sentinel/config.toml verify-storage --json`

## クイックスタート

```bash
git clone https://github.com/<your-account>/raspi-sentinel.git
cd raspi-sentinel
python3 -m pip install .
```

```bash
sudo install -d -m 0755 /etc/raspi-sentinel
sudo install -m 0644 config/raspi-sentinel.example.toml /etc/raspi-sentinel/config.toml
sudo install -d -m 0755 /var/lib/raspi-sentinel
```

```bash
sudo raspi-sentinel -c /etc/raspi-sentinel/config.toml --dry-run --verbose run-once
```

## 主な実行モード

- 単発実行: `raspi-sentinel ... run-once`
- ループ実行: `raspi-sentinel ... loop`
- 設定検証: `raspi-sentinel ... validate-config`

JSONログが必要な場合:

```bash
raspi-sentinel --structured-logging -c /etc/raspi-sentinel/config.toml run-once
```

## 重要ドキュメント

- 英語版詳細: [README.md](README.md)
- 運用手順: [docs/facts/operations-runbook.md](docs/facts/operations-runbook.md)
- データ契約: [docs/facts/data-contracts.md](docs/facts/data-contracts.md)
- テストマップ: [docs/facts/test-map.md](docs/facts/test-map.md)
- 終了コード: [docs/facts/exit-codes.md](docs/facts/exit-codes.md)
- リリースノート: [docs/release-notes/v0.6.0.md](docs/release-notes/v0.6.0.md)

テストは `tests/unit/` `tests/scenario/` `tests/e2e/` の taxonomy で運用します。
