# raspi-sentinel

`raspi-sentinel` is a small standalone logical recovery layer for Raspberry Pi services managed by `systemd`.

## Responsibility Boundary

## Core (this project)

- logical health monitoring while the OS is alive
- staged recovery policy:
  1. warn
  2. restart target services
  3. reboot only after repeated failures and safeguards

## Optional integration (outside core)

- hardware/system watchdog as lower-level failsafe for deeper hangs
- watchdog integration examples and docs are separated from core runtime logic

See: `docs/watchdog.md`

## What Problem This Solves

Many Raspberry Pi failures are logical stalls, not full kernel hangs:

- service is still running but stuck
- heartbeat/output files stop updating
- command-level checks fail repeatedly

`raspi-sentinel` detects these cases and applies deterministic, inspectable recovery actions.

## Architecture

- **Python CLI** (`raspi-sentinel` / `python -m raspi_sentinel`)
- **TOML config** as source of truth
- **Rule-based checks per target**:
  - heartbeat file freshness
  - output file freshness
  - command exit status
  - service active status
- **State file** (`/var/lib/raspi-sentinel/state.json` by default):
  - consecutive failure counters
  - last action / last reason
  - reboot history (loop guard)
  - notification follow-up schedule
- **Recovery policy**: warn -> restart -> reboot
- **Notification policy (Discord)**:
  - immediate incident notification
  - follow-up notification after delay (default 5 minutes)
  - periodic heartbeat notification (default 5 minutes)

## Recovery Flow

Per target, every cycle:

1. Evaluate configured checks
2. Healthy -> reset consecutive failures
3. Unhealthy -> increment consecutive failures
4. Apply policy:
   - below `restart_threshold`: warn
   - `>= restart_threshold`: restart services
   - `>= reboot_threshold`: reboot request (only if guards allow)

Reboot loop guards:

- minimum uptime before reboot
- reboot cooldown
- max reboot count in rolling window

## Notification Flow (Discord)

1. Incident detected: send `problem`, `action_taken`, `consecutive_failures`
2. Schedule follow-up (default `followup_delay_sec = 300`)
3. On first cycle after due time, send current status (`healthy/unhealthy`)
4. Send periodic heartbeat (`heartbeat_interval_sec`) with uptime/load/disk

## Install

## 1. Install package

```bash
git clone https://github.com/<your-account>/raspi-sentinel.git
cd raspi-sentinel
python3 -m pip install .
```

## 2. Install config

```bash
sudo install -d -m 0755 /etc/raspi-sentinel
sudo install -m 0644 config/raspi-sentinel.example.toml /etc/raspi-sentinel/config.toml
```

Edit `/etc/raspi-sentinel/config.toml` for your real services/paths.

## 3. Prepare state directory

```bash
sudo install -d -m 0755 /var/lib/raspi-sentinel
```

## 4. Install core systemd units

```bash
sudo install -m 0644 systemd/raspi-sentinel.service /etc/systemd/system/raspi-sentinel.service
sudo install -m 0644 systemd/raspi-sentinel.timer /etc/systemd/system/raspi-sentinel.timer
sudo systemctl daemon-reload
sudo systemctl enable --now raspi-sentinel.timer
```

## 5. Validate dry-run

```bash
sudo raspi-sentinel -c /etc/raspi-sentinel/config.toml --dry-run --verbose run-once
```

## Watchdog Integration (Optional)

Hardware/system watchdog is not core responsibility of `raspi-sentinel`.

- docs: `docs/watchdog.md`
- example unit: `examples/watchdog/raspi-sentinel-watchdog-integration.service`

## Logging and Auditability

Everything is logged to journald:

- failed checks with reason
- recovery actions taken
- reboot blocked/allowed reason
- Discord delivery failures

```bash
journalctl -u raspi-sentinel.service -n 200 --no-pager
journalctl -u raspi-sentinel.timer -n 200 --no-pager
```

## Discord Config Schema

```toml
[notify.discord]
enabled = true
webhook_url = "https://discord.com/api/webhooks/..."
username = "raspi-sentinel"
timeout_sec = 5
followup_delay_sec = 300
heartbeat_interval_sec = 300
```

Set `heartbeat_interval_sec = 0` to disable periodic healthy-state notifications.

## Maintenance Suppression (Optional)

Use this per target to mute checks during known maintenance windows (for example, a helper unit that intentionally restarts a service):

```toml
[[targets]]
name = "airspy_adsb"
services = ["airspy_adsb"]
service_active = true
output_file = "/run/airspy_adsb/stats.json"
output_max_age_sec = 180
command = "test -s /run/airspy_adsb/stats.json"
maintenance_mode_command = "systemctl is-active --quiet airspy_gain_guard.service"
maintenance_mode_timeout_sec = 3
maintenance_grace_sec = 90
```

When `maintenance_mode_command` exits `0`, checks for that target are skipped.  
If `maintenance_grace_sec` is set, checks stay suppressed for that many seconds after match.

## CLI Usage

One cycle:

```bash
raspi-sentinel -c /etc/raspi-sentinel/config.toml run-once
```

Continuous loop (core only):

```bash
raspi-sentinel -c /etc/raspi-sentinel/config.toml loop
```
