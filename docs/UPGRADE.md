# Upgrade and Migration Guide

This guide has two tracks:

- Track A: upgrade existing `v0.7.x` installs to current stable (`v0.8.x`)
- Track B: prepare `v0.8.x` environments for `v0.9.x` open beta validation

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
- `docs/release-notes/v0.9.0.md` (open beta release notes)

## Runtime behavior changes in v0.8.x

- `doctor` and `explain-state` commands are available.
- `stats.json` / `state.json` include explicit schema versions.
- reboot gating now uses `policy_reason` allowlist.
- config summary now warns for insecure readable config when Discord notify is enabled.

## Track B: v0.8.x -> v0.9.x open beta preparation

- if `v0.9.0` tag is not published yet, validate against `main` before rollout.
- keep recovery actions conservative until dry-run evidence is stable on your host.
- preserve `0600` config ownership and rerun `doctor --json` after each config/profile change.

## Recommended post-upgrade checks

```bash
raspi-sentinel -c /etc/raspi-sentinel/config.toml validate-config --strict
raspi-sentinel -c /etc/raspi-sentinel/config.toml doctor --json
raspi-sentinel -c /etc/raspi-sentinel/config.toml --dry-run run-once --json
raspi-sentinel -c /etc/raspi-sentinel/config.toml run-once --json
```

Optional:

```bash
# Typical Debian/Ubuntu node_exporter textfile collector path:
raspi-sentinel -c /etc/raspi-sentinel/config.toml export-prometheus --textfile-path /var/lib/node_exporter/textfile_collector/raspi_sentinel.prom
```

## Rollback

If validation fails after upgrade:

1. Stop timer temporarily:

```bash
sudo systemctl stop raspi-sentinel.timer
```

2. Restore config backup:

```bash
sudo cp -a /etc/raspi-sentinel/config.toml.bak /etc/raspi-sentinel/config.toml
```

3. If package rollback is needed, reinstall from a known-good source (examples):

```bash
# from prior tag
git checkout v0.7.1
python3 -m pip install .

# or from a previously built wheel artifact
# note: wheel filenames use underscore for distribution name (PEP 427)
python3 -m pip install ./dist/raspi_sentinel-0.7.1-py3-none-any.whl
```
4. Run `validate-config --strict` again.
5. Re-enable timer only after dry-run is healthy.

Japanese guide: [UPGRADE.ja.md](UPGRADE.ja.md)
