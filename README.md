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
  - semantic `stats.json` checks (`updated_at`, `last_input_ts`, `last_success_ts`, `records_processed_total`)
  - dependency checks split into DNS and gateway path
  - optional clock anomaly checks (`time.time` vs `time.monotonic`, optional HTTP `Date` skew)
- **State file** (`/var/lib/raspi-sentinel/state.json` by default):
  - consecutive failure counters
  - last action / last reason
  - reboot history (loop guard)
  - notification follow-up schedule
- **Event log** (`/var/lib/raspi-sentinel/events.jsonl` by default):
  - `status_change` on transitions only
  - `action_taken` for restart/reboot only
- **Monitor snapshot** (`/var/lib/raspi-sentinel/stats.json` by default):
  - raspi-sentinel writes its own current aggregate status
  - updated every 30 seconds (or immediately on status change)
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

Status model:

- `ok`
- `degraded`
- `failed`

Reason is tracked separately (`healthy`, `clock_frozen`, `clock_jump`, `clock_skewed`, `dns_error`, `gateway_error`, `stats_stale`, ...).
`dns_error` alone does not trigger reboot.

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

Transition events are also appended to `/var/lib/raspi-sentinel/events.jsonl`.
Current aggregate monitor status is exported to `/var/lib/raspi-sentinel/stats.json`.

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

## Global Snapshot Config

```toml
[global]
monitor_stats_file = "/var/lib/raspi-sentinel/stats.json"
monitor_stats_interval_sec = 30
```

Example output:

```json
{
  "service": "raspi-sentinel",
  "updated_at": "2026-04-10T21:30:00+09:00",
  "status": "ok",
  "targets_total": 4,
  "targets_ok": 4,
  "targets_degraded": 0,
  "targets_failed": 0
}
```

## Semantic Stats Schema (Recommended)

`stats.json` should expose current semantic health, for example:

```json
{
  "service": "plao",
  "updated_at": "2026-04-10T21:30:00+09:00",
  "status": "ok",
  "last_input_ts": "2026-04-10T21:29:58+09:00",
  "last_success_ts": "2026-04-10T21:29:59+09:00",
  "records_processed_total": 123456,
  "consecutive_errors": 0,
  "dns_ok": true,
  "gateway_ok": true
}
```

Use atomic writes (`stats.json.tmp` -> `rename`) and update every 30-60 seconds or on state change.

## Clock Anomaly Monitoring (Optional)

`raspi-sentinel` can detect wall-clock anomalies without modifying system time.

- no automatic `date -s` is executed
- wall clock freeze/jump is detected from `time.time()` vs `time.monotonic()`
- optional HTTP `Date` probe is used as external confirmation (`http_time_skew_sec`)
- reboot is blocked for clock-only anomalies unless:
  - anomaly is consecutive (`clock_anomaly_reboot_consecutive`)
  - dependency checks are healthy
  - HTTP probe succeeds when `http_time_probe_url` is configured

Example target fields:

```toml
[[targets]]
name = "network_uplink"
services = []
service_active = false
dns_check_command = "getent ahostsv4 blender.prod.fr24.io >/dev/null"
gateway_check_command = "ip route get 1.1.1.1 >/dev/null 2>&1 && nc -zw3 1.1.1.1 443 >/dev/null 2>&1"
time_health_enabled = true
check_interval_threshold_sec = 30
wall_clock_freeze_min_monotonic_sec = 25
wall_clock_freeze_max_wall_advance_sec = 1
wall_clock_drift_threshold_sec = 30
http_time_probe_url = "https://www.google.com"
http_time_probe_timeout_sec = 5
clock_skew_threshold_sec = 300
clock_anomaly_reboot_consecutive = 3
```

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

## Tests and CI

The goal of tests is to protect recovery policy behavior.

Priority scenarios:

- `systemd` NG (`service_active` failure) -> restart path
- `stats.json` update stop (`updated_at` stale) -> `stalled`
- `gateway_ok=true` and `dns_ok=false` (DNS-only) -> no reboot
- `last_input_ts` fresh but `last_success_ts` stale -> processing failure
- malformed/missing JSON fields -> fail safe (unhealthy)

Coverage policy:

- overall (policy modules): >= 80% with branch coverage
- core decision logic (`checks.py`, `recovery.py`): >= 90%

Run locally:

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e ".[dev]"
pytest \
  --cov=raspi_sentinel.checks \
  --cov=raspi_sentinel.config \
  --cov=raspi_sentinel.recovery \
  --cov-branch \
  --cov-report=term-missing \
  --cov-fail-under=80
python -m coverage report \
  --include="src/raspi_sentinel/checks.py,src/raspi_sentinel/recovery.py" \
  --fail-under=90
```

## License

This project is licensed under the MIT License.
See `LICENSE` for details.
