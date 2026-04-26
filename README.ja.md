# raspi-sentinel (日本語ガイド)

`raspi-sentinel` は Raspberry Pi 上の `systemd` 管理サービスを監視し、
`warn -> restart -> reboot` の段階的リカバリを行う小さな監視/復旧レイヤーです。

## Open Beta: v0.9.x

試してほしい人:

- `systemd` で Raspberry Pi サービスを運用している
- ログ確認と TOML 編集ができる
- まず dry-run から始められる

まだ使うべきではない人:

- 初日から無人本番復旧が必要
- 想定外 reboot が危険
- 物理または代替経路でマシンへアクセスできない

## 15分クイックスタート（beta）

1. install

```bash
pipx install "git+https://github.com/yukimurata0421/raspi-sentinel.git@main"
```

2. 最小 config 配置

```bash
sudo install -d -m 0755 /etc/raspi-sentinel
sudo install -m 0600 -o root -g root config/raspi-sentinel.example.toml /etc/raspi-sentinel/config.toml
```

3. validate-config

```bash
raspi-sentinel -c /etc/raspi-sentinel/config.toml validate-config --strict
```

4. doctor

```bash
raspi-sentinel -c /etc/raspi-sentinel/config.toml doctor --json
```

5. run-once --dry-run --json

```bash
raspi-sentinel -c /etc/raspi-sentinel/config.toml --dry-run run-once --json
```

6. サンプル障害注入

```bash
python3 scripts/failure_inject.py stale-file --path /tmp/heartbeat.txt --age-sec 900
```

7. events/state 確認

```bash
tail -n 20 /var/lib/raspi-sentinel/events.jsonl
cat /var/lib/raspi-sentinel/state.json
```

8. disable

```bash
sudo systemctl disable --now raspi-sentinel.timer
```

## 既知の制約 / 非目標

- ハードウェア watchdog の代替ではない
- フリート監視システムではない
- すべての Raspberry Pi OS リリースで検証済みではない
- reboot は dry-run 検証後に有効化すべき
- Discord webhook は必ず保護する

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
- Prometheus出力: `raspi-sentinel ... export-prometheus --textfile-path <path>`

権限修正（doctor から実行）:

```bash
sudo raspi-sentinel -c /etc/raspi-sentinel/config.toml doctor --json --fix-permissions
```

サポートバンドル出力（beta報告向け、秘匿情報マスク）:

```bash
raspi-sentinel -c /etc/raspi-sentinel/config.toml doctor --json --support-bundle ./support-bundle.json
```

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
- アップグレードガイド: [docs/UPGRADE.ja.md](docs/UPGRADE.ja.md)
- セキュリティポリシー: [docs/SECURITY.ja.md](docs/SECURITY.ja.md)

## Beta フィードバック

- GitHub の `Beta failure report` フォームを使用
- 実行コマンド、期待結果、実際結果、doctor/support-bundle を添付
- webhook URL・token・個人識別情報は貼り付けない

テストは `tests/unit/` `tests/scenario/` `tests/e2e/` の taxonomy で運用します。
