# raspi-sentinel 日本語ガイド

`raspi-sentinel` は Raspberry Pi 上の `systemd` 管理サービスを監視し、論理停止時に段階的な復旧を行う小さな監視/復旧レイヤーです。

```text
warn -> restart services -> guarded reboot
```

`v0.9.x` は次期 open beta（準備中）です。現行のリリースラインは `v0.8.0` です。

詳細仕様は英語 README と `docs/` に分離しています。ここでは「安全に試して報告する入口」に絞ります。

## 試してほしい人

- Raspberry Pi で常時稼働サービスを運用している
- `systemd` / TOML / `journalctl` を扱える
- まず dry-run で挙動確認できる
- GitHub Issue で再現手順つき報告ができる

## まだ使うべきではない人

- 想定外 reboot が危険な環境
- 初日から無人本番復旧が必要な環境
- 物理または代替アクセス手段がない環境
- 複数台集中監視ダッシュボードを求めている環境

## どのバージョンを使うか

### stable: `v0.8.0`

現行のリリース版を使う場合:

```bash
git clone https://github.com/yukimurata0421/raspi-sentinel.git
cd raspi-sentinel
git checkout v0.8.0
```

`v0.8.0` の運用手順は、そのタグに含まれる README を参照してください。

### 次期 beta プレビュー: `main`

`v0.9.0` タグ作成前のプレビューを試す場合のみ:

```bash
git clone https://github.com/yukimurata0421/raspi-sentinel.git
cd raspi-sentinel
git checkout main
```

`v0.9.0` タグ公開後は、再現性のため `git checkout v0.9.0` を使用してください。

## 15分ベータデモ

### 1. beta プレビューラインを clone

```bash
git clone https://github.com/yukimurata0421/raspi-sentinel.git
cd raspi-sentinel
git checkout main
```

このベータデモは、`v0.9.0` タグ前の beta プレビュー資材（`main`）を前提にしています。

### 2. install

```bash
python3 -m pip install .
```

### 3. デモ用ワークスペースと config 準備（restart/rebootなし、通知なし）

```bash
install -d -m 0755 /tmp/raspi-sentinel-demo
cp config/raspi-sentinel.beta-demo.toml /tmp/raspi-sentinel-demo/config.toml
${EDITOR:-vi} /tmp/raspi-sentinel-demo/config.toml
```

### 4. config 検証

```bash
raspi-sentinel -c /tmp/raspi-sentinel-demo/config.toml validate-config --strict
```

### 5. doctor

```bash
raspi-sentinel -c /tmp/raspi-sentinel-demo/config.toml doctor --json
```

### 6. デモ heartbeat 初期化（正常系ベースライン）

```bash
python3 scripts/failure_inject.py fresh-file --path /tmp/raspi-sentinel-demo/heartbeat.txt
```

### 7. dry-run（最初は正常判定を確認）

```bash
raspi-sentinel -c /tmp/raspi-sentinel-demo/config.toml --dry-run run-once --json
```

### 8. サンプル障害注入

```bash
python3 scripts/failure_inject.py stale-file --path /tmp/raspi-sentinel-demo/heartbeat.txt --age-sec 900
```

再度 dry-run:

```bash
raspi-sentinel -c /tmp/raspi-sentinel-demo/config.toml --dry-run run-once --json
```

### 9. 確認と停止

```bash
tail -n 20 /tmp/raspi-sentinel-demo/events.jsonl
raspi-sentinel -c /tmp/raspi-sentinel-demo/config.toml explain-state --json
sudo systemctl disable --now raspi-sentinel.timer
```

### 10. デモファイル削除（任意）

```bash
rm -rf /tmp/raspi-sentinel-demo
```

## 本番向けセットアップ（root所有 config）

本番/systemd 運用では `/etc` 配下に root:root / 0600 で配置します。

```bash
sudo install -d -m 0755 /etc/raspi-sentinel
sudo install -m 0600 -o root -g root config/raspi-sentinel.example.toml /etc/raspi-sentinel/config.toml
sudo "${EDITOR:-vi}" /etc/raspi-sentinel/config.toml
raspi-sentinel -c /etc/raspi-sentinel/config.toml validate-config --strict
```

## 緊急停止

```bash
sudo systemctl disable --now raspi-sentinel.timer
sudo systemctl stop raspi-sentinel.service
```

## systemd timer 有効化（dry-run確認後）

`install_systemd.py` は `raspi-sentinel` の実バイナリパスを検出して unit を描画します。

```bash
BIN="$(command -v raspi-sentinel)"
sudo python3 scripts/install_systemd.py --raspi-sentinel-bin "$BIN" --enable-timer
```

付属 service は `ProtectHome=true` のため、`ExecStart` のバイナリは `/home/...` 以外を推奨します。
`/opt/raspi-sentinel/.venv/bin/raspi-sentinel` のような systemd から見えるパスを使ってください。

tmpfs tiering を使う場合:

```bash
BIN="$(command -v raspi-sentinel)"
sudo python3 scripts/install_systemd.py --raspi-sentinel-bin "$BIN" --include-tmpfs-mount --enable-timer
```

`--dry-run` は restart/reboot を止め、通知送信もデフォルトで抑制します。通知経路を明示的に試すときだけ `--send-notifications` を付けてください。

補足: 付属 systemd unit は `ProtectHome=true` です。`/home` 配下の監視パスは手動 dry-run で読めても timer 実行時に失敗する場合があります。

## フィードバックしてほしいこと

- install で詰まった箇所
- `validate-config` / `doctor` が分かりにくい箇所
- dry-run の不自然な判定
- false positive / false negative
- systemd timer/service の問題
- docs の迷子ポイント

Issue 作成前の推奨コマンド:

```bash
raspi-sentinel --version
python3 --version
systemctl --version
raspi-sentinel -c /etc/raspi-sentinel/config.toml validate-config --strict
raspi-sentinel -c /etc/raspi-sentinel/config.toml doctor --json
raspi-sentinel -c /etc/raspi-sentinel/config.toml --dry-run run-once --json
raspi-sentinel -c /etc/raspi-sentinel/config.toml doctor --json --support-bundle ./support-bundle.json
```

Issue テンプレート:

- [Beta failure report](../../issues/new/choose)

公開 Issue に貼らないもの:

- Discord webhook URL
- token
- private hostname
- 個人識別につながるパス

## 詳細ドキュメント

- [English README (EN)](README.md)
- [Documentation Index (EN)](docs/README.md)
- [Upgrade Guide (JA)](docs/UPGRADE.ja.md)
- [Security Policy (JA)](docs/SECURITY.ja.md)
- [Output Contract (EN)](docs/output-contract.md)
- [Storage Tiers (EN)](docs/storage-tiers.md)
- [Watchdog Integration (EN)](docs/watchdog.md)
