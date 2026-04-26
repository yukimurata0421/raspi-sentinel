# アップグレード / マイグレーションガイド

このガイドは次の 2 つを対象にしています。

- Track A: `v0.7.x` から現行 stable (`v0.8.x`) への更新
- Track B: `v0.8.x` 環境で次期 `v0.9.x` open beta の検証準備

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
- `docs/release-notes/v0.9.0.md`（次期 beta のドラフト）

## v0.8.x の主な変更点

- `doctor` / `explain-state` コマンド追加
- `stats.json` / `state.json` に schema version 追加
- reboot 判定が `policy_reason` allowlist ベースに変更
- Discord 有効時に config が group/other readable なら warning

## Track B: v0.8.x -> v0.9.x open beta 準備

- `v0.9.x` はまだドラフト段階なので、導入前に `main` で挙動を検証してから適用
- recovery action は dry-run の観測が安定するまで保守的に維持
- config 権限 (`0600`) を維持し、設定変更後に `doctor --json` を再実行

## 更新後の推奨確認

```bash
raspi-sentinel -c /etc/raspi-sentinel/config.toml validate-config --strict
raspi-sentinel -c /etc/raspi-sentinel/config.toml doctor --json
raspi-sentinel -c /etc/raspi-sentinel/config.toml --dry-run run-once --json
raspi-sentinel -c /etc/raspi-sentinel/config.toml run-once --json
```

任意:

```bash
# Debian/Ubuntu の一般的な node_exporter textfile collector パス例:
raspi-sentinel -c /etc/raspi-sentinel/config.toml export-prometheus --textfile-path /var/lib/node_exporter/textfile_collector/raspi_sentinel.prom
```

## ロールバック

更新後に検証失敗した場合:

1. timer を一時停止:

```bash
sudo systemctl stop raspi-sentinel.timer
```

2. config バックアップを復元:

```bash
sudo cp -a /etc/raspi-sentinel/config.toml.bak /etc/raspi-sentinel/config.toml
```

3. 必要な場合は旧バージョンを再インストール（例）:

```bash
python3 -m pip install 'raspi-sentinel==<previous-version>'
```

4. `validate-config --strict` を再実行
5. dry-run が健全になってから timer を再有効化

English guide: [UPGRADE.md](UPGRADE.md)
