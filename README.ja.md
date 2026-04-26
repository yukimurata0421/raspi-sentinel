# raspi-sentinel 日本語ガイド

`raspi-sentinel` は Raspberry Pi 上の `systemd` 管理サービスを監視し、論理停止時に段階的な復旧を行う小さな監視/復旧レイヤーです。

```text
warn -> restart services -> guarded reboot
```

現在の `v0.9.x` は open beta です。

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

## 15分クイックスタート

### 1. beta tag を clone

```bash
git clone https://github.com/yukimurata0421/raspi-sentinel.git
cd raspi-sentinel
git checkout v0.9.0
```

### 2. install

```bash
python3 -m pip install .
```

### 3. config 配置

```bash
sudo install -d -m 0755 /etc/raspi-sentinel
sudo install -m 0600 -o root -g root config/raspi-sentinel.example.toml /etc/raspi-sentinel/config.toml
sudo editor /etc/raspi-sentinel/config.toml
```

### 4. config 検証

```bash
raspi-sentinel -c /etc/raspi-sentinel/config.toml validate-config --strict
```

### 5. doctor

```bash
raspi-sentinel -c /etc/raspi-sentinel/config.toml doctor --json
```

### 6. dry-run

```bash
raspi-sentinel -c /etc/raspi-sentinel/config.toml --dry-run run-once --json
```

### 7. サンプル障害注入

```bash
python3 scripts/failure_inject.py stale-file --path /tmp/heartbeat.txt --age-sec 900
```

再度 dry-run:

```bash
raspi-sentinel -c /etc/raspi-sentinel/config.toml --dry-run run-once --json
```

### 8. 確認と停止

```bash
tail -n 20 /var/lib/raspi-sentinel/events.jsonl
raspi-sentinel -c /etc/raspi-sentinel/config.toml explain-state --json
sudo systemctl disable --now raspi-sentinel.timer
```

## 緊急停止

```bash
sudo systemctl disable --now raspi-sentinel.timer
sudo systemctl stop raspi-sentinel.service
```

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

- [English README](README.md)
- [Documentation Index](docs/README.md)
- [Upgrade Guide](docs/UPGRADE.ja.md)
- [Security Policy](docs/SECURITY.ja.md)
- [Output Contract](docs/output-contract.md)
- [Storage Tiers](docs/storage-tiers.md)
- [Watchdog Integration](docs/watchdog.md)
