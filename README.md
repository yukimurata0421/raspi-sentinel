# raspi-sentinel

[![CI](https://github.com/yukimurata0421/raspi-sentinel/actions/workflows/ci.yml/badge.svg)](https://github.com/yukimurata0421/raspi-sentinel/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/yukimurata0421/raspi-sentinel?sort=semver)](https://github.com/yukimurata0421/raspi-sentinel/releases)
[![License: MIT](https://img.shields.io/github/license/yukimurata0421/raspi-sentinel)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.11%20%7C%203.12-3776AB?logo=python&logoColor=white)](pyproject.toml)
[![Tests](https://img.shields.io/badge/tests-unit%20%7C%20scenario%20%7C%20e2e-0A7F2E)](docs/facts/test-map.md)

`raspi-sentinel` is a guarded recovery supervisor for Raspberry Pi services managed by `systemd`.

It detects logical stalls (stale files, failed command checks, stale status JSON, dependency failures) and applies staged recovery:

```text
warn -> restart services -> guarded reboot
```

Japanese guide: [README.ja.md](README.ja.md)

## Open Beta Preview: v0.9.x (Upcoming)

`v0.9.x` is the next planned open beta line before `v1.0.0`.
Current released line is `v0.8.0`.

Who should try this:

- You run Raspberry Pi services with `systemd`
- You can inspect logs and edit TOML
- You are willing to start with dry-run mode
- You can report reproducible failures through GitHub Issues

Do not use yet if:

- You need unattended production recovery from day one
- Unexpected reboot would be dangerous
- You cannot access the machine physically or via fallback path
- You need fleet-level centralized monitoring

## What This Does

- Monitors local service health while OS is alive
- Evaluates file freshness, command checks, service active state, semantic stats, external status JSON, dependency probes, optional clock checks
- Applies deterministic staged recovery with cooldown/window guards
- Persists inspectable state/events for postmortem

Network-only failures (DNS/gateway path) are excluded from direct reboot reasons by default.

## Output Model

`raspi-sentinel` separates runtime outputs by role:

- `state.json`: recovery-oriented engine state
- `stats.json`: current operational snapshot for operators and integrations
- `events.jsonl`: append-only transition/audit trail for postmortem

## What This Does Not Do

- Hardware watchdog replacement
- Full kernel/hardware hang detector
- Fleet monitoring control plane
- Internet-facing/multi-tenant control plane
- Untrusted command execution tool

## Safety Model

`raspi-sentinel` assumes trusted operator-controlled config on a single machine.

Recommended config ownership:

```bash
sudo chown root:root /etc/raspi-sentinel/config.toml
sudo chmod 0600 /etc/raspi-sentinel/config.toml
```

Why this matters:

- Config may contain webhook URLs and command arguments
- Recovery actions may restart services or request reboot
- `shell=False` is default; shell execution is explicit opt-in

## 15-Minute Beta Demo

### 1. Clone current release tag

```bash
git clone https://github.com/yukimurata0421/raspi-sentinel.git
cd raspi-sentinel
git checkout v0.8.0
```

If you intentionally test the upcoming beta draft work, use `main` instead.

### 2. Install

```bash
python3 -m pip install .
```

### 3. Install demo config (no restart/reboot, no notifications)

```bash
sudo install -d -m 0755 /etc/raspi-sentinel
sudo install -m 0600 -o root -g root config/raspi-sentinel.beta-demo.toml /etc/raspi-sentinel/config.toml
sudo "${EDITOR:-vi}" /etc/raspi-sentinel/config.toml
```

### 4. Validate config

```bash
raspi-sentinel -c /etc/raspi-sentinel/config.toml validate-config --strict
```

### 5. Run doctor

```bash
raspi-sentinel -c /etc/raspi-sentinel/config.toml doctor --json
```

### 6. Initialize demo heartbeat (healthy baseline)

```bash
python3 scripts/failure_inject.py fresh-file --path /tmp/raspi-sentinel-demo/heartbeat.txt
```

### 7. Run one dry-run cycle (expected healthy)

```bash
raspi-sentinel -c /etc/raspi-sentinel/config.toml --dry-run run-once --json
```

### 8. Inject sample failure

```bash
sudo install -d -m 0755 /tmp/raspi-sentinel-demo
python3 scripts/failure_inject.py stale-file --path /tmp/raspi-sentinel-demo/heartbeat.txt --age-sec 900
```

Then run dry-run again:

```bash
raspi-sentinel -c /etc/raspi-sentinel/config.toml --dry-run run-once --json
```

### 9. Inspect and stop

```bash
tail -n 20 /var/lib/raspi-sentinel/events.jsonl
raspi-sentinel -c /etc/raspi-sentinel/config.toml explain-state --json
sudo systemctl disable --now raspi-sentinel.timer
```

## Emergency Stop

```bash
sudo systemctl disable --now raspi-sentinel.timer
sudo systemctl stop raspi-sentinel.service
```

## Enable Timer (After Dry-run Verification)

Install units using helper (renders `ExecStart` with detected `raspi-sentinel` binary path):

```bash
BIN="$(command -v raspi-sentinel)"
sudo python3 scripts/install_systemd.py --raspi-sentinel-bin "$BIN" --enable-timer
```

The helper is recommended because it renders `ExecStart` to your actual binary path.
Manual installation is possible, but it is not equivalent unless you install all required units and edit `ExecStart` yourself:

```bash
sudo install -m 0644 systemd/raspi-sentinel.service /etc/systemd/system/raspi-sentinel.service
sudo install -m 0644 systemd/raspi-sentinel.timer /etc/systemd/system/raspi-sentinel.timer
sudo install -m 0644 systemd/raspi-sentinel-tmpfs-verify.service /etc/systemd/system/raspi-sentinel-tmpfs-verify.service
sudo systemctl daemon-reload
sudo systemctl enable --now raspi-sentinel.timer
```

If `[storage].require_tmpfs = true` or tmpfs tiering is configured, include mount unit install:

```bash
BIN="$(command -v raspi-sentinel)"
sudo python3 scripts/install_systemd.py --raspi-sentinel-bin "$BIN" --include-tmpfs-mount --enable-timer
```

`--dry-run` disables restart/reboot and suppresses external notifications by default.
Use `--send-notifications` only when you intentionally want notification path testing in dry-run.

When running under the bundled systemd service, `ProtectHome=true` is enabled.
Paths under `/home` can work in manual CLI dry-run but fail when timer execution starts.

## Feedback Wanted (v0.9.x Upcoming)

Please report:

- install failures
- confusing config validation / doctor output
- dry-run behavior that looks unsafe or unclear
- false positives / false negatives
- systemd timer/service integration issues
- failure injection outcomes
- docs gaps

Use issue form:

- [Open beta feedback issue](../../issues/new/choose)

Helpful report commands:

```bash
raspi-sentinel --version
python3 --version
systemctl --version
raspi-sentinel -c /etc/raspi-sentinel/config.toml validate-config --strict
raspi-sentinel -c /etc/raspi-sentinel/config.toml doctor --json
raspi-sentinel -c /etc/raspi-sentinel/config.toml --dry-run run-once --json
raspi-sentinel -c /etc/raspi-sentinel/config.toml doctor --json --support-bundle ./support-bundle.json
```

Do not post secrets in public issues:

- webhook URLs
- tokens
- private hostnames
- sensitive local paths

## Documentation Map

Start here:

- [Documentation Index](docs/README.md)
- [Upgrade Guide](docs/UPGRADE.md)
- [Security Policy](SECURITY.md)
- [Output Contract](docs/output-contract.md)
- [Storage Tiers](docs/storage-tiers.md)
- [Watchdog Integration](docs/watchdog.md)
- [Time Health Decision Table](docs/time-health-decision-table.md)
- [Operations Runbook](docs/facts/operations-runbook.md)

Tests and CI details: [docs/facts/test-map.md](docs/facts/test-map.md)

## Versioning

Current line: `v0.8.x` stable release.
Next planned line: `v0.9.x` open beta.

See:

- [CHANGELOG.md](CHANGELOG.md)
- [docs/VERSIONING.md](docs/VERSIONING.md)

## License

MIT License.
