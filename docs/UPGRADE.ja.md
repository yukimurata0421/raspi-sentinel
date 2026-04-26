# アップグレード / マイグレーションガイド

このガイドは `v0.7.x` から `v0.8.x` への更新を対象にしています。

## 事前チェック

1. config と state を退避:

```bash
sudo cp -a /etc/raspi-sentinel/config.toml /etc/raspi-sentinel/config.toml.bak
sudo cp -a /var/lib/raspi-sentinel /var/lib/raspi-sentinel.bak.$(date +%Y%m%dT%H%M%S)
```

2. 現行設定を厳格検証:

```bash
raspi-sentinel -c /etc/raspi-sentinel/config.toml validate-config --strict
```

3. 対象タグのリリースノート確認:

- `docs/release-notes/v0.8.0.md`

## v0.8.x の主な変更点

- `doctor` / `explain-state` コマンド追加
- `stats.json` / `state.json` に schema version 追加
- reboot 判定が `policy_reason` allowlist ベースに変更
- Discord 有効時に config が group/other readable なら warning

## 更新後の推奨確認

```bash
raspi-sentinel -c /etc/raspi-sentinel/config.toml validate-config --strict
raspi-sentinel -c /etc/raspi-sentinel/config.toml doctor --json
raspi-sentinel -c /etc/raspi-sentinel/config.toml --dry-run run-once --json
raspi-sentinel -c /etc/raspi-sentinel/config.toml run-once --json
```

任意:

```bash
raspi-sentinel -c /etc/raspi-sentinel/config.toml export-prometheus --textfile-path /var/lib/node_exporter/textfile_collector/raspi_sentinel.prom
```

## ロールバック

更新後に検証失敗した場合:

1. timer を一時停止:

```bash
sudo systemctl stop raspi-sentinel.timer
```

2. 旧バージョンのファイルと config バックアップを復元
3. `validate-config --strict` を再実行
4. dry-run が健全になってから timer を再有効化

English guide: [UPGRADE.md](UPGRADE.md)
