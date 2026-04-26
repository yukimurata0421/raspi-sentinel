# Upgrade and Migration Guide

This guide focuses on upgrading from `v0.7.x` to `v0.8.x`.

## Pre-upgrade checklist

1. Backup current config/state:

```bash
sudo cp -a /etc/raspi-sentinel/config.toml /etc/raspi-sentinel/config.toml.bak
sudo cp -a /var/lib/raspi-sentinel /var/lib/raspi-sentinel.bak.$(date +%Y%m%dT%H%M%S)
```

2. Validate current config before switching:

```bash
raspi-sentinel -c /etc/raspi-sentinel/config.toml validate-config --strict
```

3. Confirm release notes for target tag:

- `docs/release-notes/v0.8.0.md`

## Runtime behavior changes in v0.8.x

- `doctor` and `explain-state` commands are available.
- `stats.json` / `state.json` include explicit schema versions.
- reboot gating now uses `policy_reason` allowlist.
- config summary now warns for insecure readable config when Discord notify is enabled.

## Recommended post-upgrade checks

```bash
raspi-sentinel -c /etc/raspi-sentinel/config.toml validate-config --strict
raspi-sentinel -c /etc/raspi-sentinel/config.toml doctor --json
raspi-sentinel -c /etc/raspi-sentinel/config.toml --dry-run run-once --json
raspi-sentinel -c /etc/raspi-sentinel/config.toml run-once --json
```

Optional:

```bash
raspi-sentinel -c /etc/raspi-sentinel/config.toml export-prometheus --textfile-path /var/lib/node_exporter/textfile_collector/raspi_sentinel.prom
```

## Rollback

If validation fails after upgrade:

1. Stop timer temporarily:

```bash
sudo systemctl stop raspi-sentinel.timer
```

2. Restore previous package/files and config backup.
3. Run `validate-config --strict` again.
4. Re-enable timer only after dry-run is healthy.

Japanese guide: [UPGRADE.ja.md](UPGRADE.ja.md)
