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
- プロファイル別設定例:
  - `config/examples/production.toml`
  - `config/examples/lightweight-pi.toml`
  - `config/examples/no-discord.toml`
  - `config/examples/tmpfs-tiered.toml`
- 検証コマンド: `raspi-sentinel -c /etc/raspi-sentinel/config.toml verify-storage --json`

## Non-goals（非目標）

- 本リリースでは「完全な SD フリー運用」はサポートしません。
  `events.jsonl` と安全ガードに必要な永続状態はディスク保持を前提にします。
- この機能はシステム層の耐障害対策
  （電源断対策、ファイルシステム点検、ハードウェア watchdog 戦略）を置き換えるものではありません。

## クイックスタート

### インストール方法 A（ソースから）

```bash
git clone https://github.com/yukimurata0421/raspi-sentinel.git
cd raspi-sentinel
git config core.hooksPath .githooks
python3 -m pip install .
```

### インストール方法 B（`pipx` + GitHub）

```bash
pipx install "git+https://github.com/yukimurata0421/raspi-sentinel.git@main"
```

### インストール方法 C（PyPI 公開後）

```bash
pipx install raspi-sentinel
# または
python3 -m pip install raspi-sentinel
```

```bash
sudo install -d -m 0755 /etc/raspi-sentinel
sudo install -m 0600 -o root -g root config/raspi-sentinel.example.toml /etc/raspi-sentinel/config.toml
sudo install -d -m 0755 /var/lib/raspi-sentinel
```

```bash
sudo raspi-sentinel -c /etc/raspi-sentinel/config.toml --dry-run --verbose run-once
```

### Docker（dry-run 専用の簡易チェック）

```bash
docker build -f docker/Dockerfile.dryrun -t raspi-sentinel:dryrun .
docker run --rm \
  -v /etc/raspi-sentinel:/config:ro \
  -v /var/lib/raspi-sentinel:/var/lib/raspi-sentinel \
  raspi-sentinel:dryrun
```

このイメージは `run-once` のみ許可し、常に `--dry-run` を強制します。

push 前チェック:

```bash
bash scripts/prepush_check.sh
```

## 主な実行モード

- 単発実行: `raspi-sentinel ... run-once`
- ループ実行: `raspi-sentinel ... loop`
- 設定検証: `raspi-sentinel ... validate-config`
- 運用事前診断: `raspi-sentinel ... doctor`
- 状態説明: `raspi-sentinel ... explain-state`

JSONログが必要な場合:

```bash
raspi-sentinel --structured-logging -c /etc/raspi-sentinel/config.toml run-once
```

## 重要ドキュメント

- 英語版詳細: [README.md](README.md)
- 運用手順: [docs/facts/operations-runbook.md](docs/facts/operations-runbook.md)
- データ契約: [docs/facts/data-contracts.md](docs/facts/data-contracts.md)
- 出力契約: [docs/output-contract.md](docs/output-contract.md)
- テストマップ: [docs/facts/test-map.md](docs/facts/test-map.md)
- 終了コード: [docs/facts/exit-codes.md](docs/facts/exit-codes.md)
- リリースノート: [docs/release-notes/v0.8.0.md](docs/release-notes/v0.8.0.md)

テストは `tests/unit/` `tests/scenario/` `tests/e2e/` の taxonomy で運用します。
