# raspi-sentinel

`raspi-sentinel` is a small standalone logical recovery layer for Raspberry Pi services managed by `systemd`.

## Responsibility Boundary

## Security model (read this)

`raspi-sentinel` is designed for **trusted operator-controlled configuration** on a single machine (for example `/etc/raspi-sentinel/config.toml` owned by root).

- **Not** a multi-tenant or internet-facing control plane: do not pass untrusted user input into config fields.
- Config commands run with `shell=False` by default.
- Shell execution is explicit opt-in via `command_use_shell`, `dns_check_use_shell`, `gateway_check_use_shell`, `maintenance_mode_use_shell`.
- If shell syntax is detected without opt-in, the check fails safely and is recorded.
- When running as **root**, restrict config file permissions (for example `chmod 600` / `root:root`) so webhook URLs and commands are not exposed to other local users.
- On load, if the config file is **group- or world-writable**, a **warning** is logged (unsafe in shared-admin environments).

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
  - if state is corrupted, file is quarantined to `state.json.corrupt.<timestamp>` and cycle enters limited mode
- **Event log** (`/var/lib/raspi-sentinel/events.jsonl` by default):
  - status/reason transitions only (no duplicate lines while state unchanged)
  - `action` field for `restart` / `reboot` outcomes
  - optional `kind: notify_delivery_failed` when Discord delivery fails (after retries)
  - optional `kind: state_corrupted` / `kind: state_load_error` when state loading degrades
  - optional **size-based rotation** via `[global].events_max_file_bytes` (renames to `events.jsonl.1` when exceeded; `0` disables)
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
state_max_file_bytes = 2000000
state_reboots_max_entries = 256
state_lock_timeout_sec = 5
monitor_stats_file = "/var/lib/raspi-sentinel/stats.json"
monitor_stats_interval_sec = 30
events_max_file_bytes = 5000000
events_backup_generations = 3
```

Set `events_max_file_bytes = 0` to disable rotation of `events.jsonl`.
Set `state_max_file_bytes = 0` to disable `state.json` size guard.
`events_backup_generations` controls how many rotated files are kept (`events.jsonl.1..N`).

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
dns_check_use_shell = true
gateway_check_command = "ip route get 1.1.1.1 >/dev/null 2>&1 && nc -zw3 1.1.1.1 443 >/dev/null 2>&1"
gateway_check_use_shell = true
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

One cycle with machine-readable output:

```bash
raspi-sentinel -c /etc/raspi-sentinel/config.toml run-once --json
```

`run-once --json` is reporting-enabled execution, not evaluation-only mode.
It runs the same recovery/event/state-persistence flow as `run-once`.

Example:

```json
{
  "updated_at": "2026-04-10T21:30:00+09:00",
  "overall_status": "degraded",
  "dry_run": true,
  "reboot_requested": false,
  "targets": {
    "network_uplink": {
      "status": "degraded",
      "reason": "dns_error",
      "action": "warn",
      "healthy": false,
      "evidence": {
        "dns_ok": false,
        "gateway_ok": true
      }
    }
  }
}
```

Continuous loop (core only):

```bash
raspi-sentinel -c /etc/raspi-sentinel/config.toml loop
```

Validate config before enabling systemd timer/service:

```bash
raspi-sentinel -c /etc/raspi-sentinel/config.toml validate-config
```

JSON summary output (for automation):

```bash
raspi-sentinel -c /etc/raspi-sentinel/config.toml validate-config --json
```

Fail validation when warnings exist:

```bash
raspi-sentinel -c /etc/raspi-sentinel/config.toml validate-config --strict
```

### Exit codes (`run-once`)

| Code | Meaning |
|------|--------|
| `0` | All targets **ok** this cycle |
| `1` | At least one target **degraded** or **failed** |
| `2` | A **reboot** was requested (process exits before the reboot) |
| `10` | Config load error |
| `11` | Invalid loop interval |
| `12` | No subcommand / help |
| `13` | State lock timeout (another cycle holds the lock) |
| `14` | State persistence failed |
| `15` | `validate-config --strict` found warnings |

Use `0` vs `1` / `2` in systemd `ExecStart=` or scripts if you alert on unhealthy cycles or reboot requests.

## `stats.json` vs `events.jsonl`

- `stats.json`: current snapshot ("now"), overwritten atomically.
- `events.jsonl`: append-only transition/history log ("what changed"), written only on status/reason/action changes.

Example `events.jsonl` lines:

```json
{"ts":"2026-04-10T21:25:00+09:00","service":"network_uplink","from":"ok","to":"degraded","reason":"dns_error","dns_ok":false,"gateway_ok":true}
{"ts":"2026-04-10T21:27:00+09:00","service":"network_uplink","from":"degraded","to":"degraded","reason":"dns_error","action":"warn","dns_ok":false,"gateway_ok":true}
{"ts":"2026-04-10T21:31:00+09:00","service":"network_uplink","from":"degraded","to":"ok","reason":"healthy"}
```

## Guarantees and Non-Guarantees

Guaranteed:

- explicit status/reason decisions per target (`ok` / `degraded` / `failed`)
- staged recovery policy with safeguards (warn/restart/reboot)
- transition evidence persisted to `events.jsonl`
- no direct wall-clock correction (`date -s` is not executed)

Not guaranteed:

- full kernel/hardware hang detection on its own (use watchdog/external monitor if required)
- perfect root-cause certainty from a single probe/sample
- automatic time correction or NTP repair logic

## Tests and CI

The goal of tests is to protect recovery policy behavior.

Priority scenarios:

- `systemd` NG (`service_active` failure) -> restart path
- `stats.json` update stop (`updated_at` stale) -> `stalled`
- `gateway_ok=true` and `dns_ok=false` (DNS-only) -> no reboot
- `last_input_ts` fresh but `last_success_ts` stale -> processing failure
- malformed/missing JSON fields -> fail safe (unhealthy)

Coverage policy (matches CI):

- Tracked modules: `checks`, `config`, `recovery`, **`policy`**, **`status_events`**, **`time_health`** — overall **≥ 80%** (branch coverage on).
- **Policy + `status_events`**: dedicated report **≥ 85%**.
- **`checks` + `recovery`**: dedicated report **≥ 88%**.

Run locally:

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e ".[dev]"
ruff check src tests
ruff format --check src tests
pytest \
  --cov=raspi_sentinel.checks \
  --cov=raspi_sentinel.config \
  --cov=raspi_sentinel.recovery \
  --cov=raspi_sentinel.policy \
  --cov=raspi_sentinel.status_events \
  --cov=raspi_sentinel.time_health \
  --cov-branch \
  --cov-report=term-missing \
  --cov-fail-under=80
python -m coverage report \
  --include="src/raspi_sentinel/policy.py,src/raspi_sentinel/status_events.py" \
  --fail-under=85
python -m coverage report \
  --include="src/raspi_sentinel/checks.py,src/raspi_sentinel/recovery.py" \
  --fail-under=88
```

## Versioning

- **Current release line:** **0.4.1** (see `CHANGELOG.md`).
- **Single version string:** `src/raspi_sentinel/_version.py` (`raspi_sentinel.__version__`). `pyproject.toml` reads it at build time (no duplicate number).
- **Git tags:** use `v0.4.1` (or current `__version__`) for releases. An older **`v0.2.0`** tag may exist as a snapshot — details in [docs/VERSIONING.md](docs/VERSIONING.md).

## License

This project is licensed under the MIT License.
See `LICENSE` for details.
