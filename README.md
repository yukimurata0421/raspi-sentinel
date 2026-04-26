# raspi-sentinel

[![CI](https://github.com/yukimurata0421/raspi-sentinel/actions/workflows/ci.yml/badge.svg)](https://github.com/yukimurata0421/raspi-sentinel/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/yukimurata0421/raspi-sentinel?sort=semver)](https://github.com/yukimurata0421/raspi-sentinel/releases)
[![License: MIT](https://img.shields.io/github/license/yukimurata0421/raspi-sentinel)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.11%20%7C%203.12-3776AB?logo=python&logoColor=white)](pyproject.toml)
[![Lint: Ruff](https://img.shields.io/badge/lint-ruff-46A2F1?logo=ruff&logoColor=white)](pyproject.toml)
[![Type Check: mypy strict](https://img.shields.io/badge/type%20check-mypy%20strict-2A6DB2)](pyproject.toml)
[![Tests](https://img.shields.io/badge/tests-unit%20%7C%20scenario%20%7C%20e2e-0A7F2E)](docs/facts/test-map.md)
[![Coverage Gates](https://img.shields.io/badge/coverage%20gates-80%2F85%2F88%2F90-0A7F2E)](.github/workflows/ci.yml)

`raspi-sentinel` is a small standalone logical recovery layer for Raspberry Pi services managed by `systemd`.

Japanese guide: [README.ja.md](README.ja.md)

## Responsibility Boundary

This section defines what `raspi-sentinel` is responsible for and what remains outside of its scope.

### Security model (read this)

`raspi-sentinel` is designed for **trusted operator-controlled configuration** on a single machine (for example `/etc/raspi-sentinel/config.toml` owned by root).

- **Not** a multi-tenant or internet-facing control plane: do not pass untrusted user input into config fields.
- Config commands run with `shell=False` by default.
- Shell execution is explicit opt-in via `command_use_shell`, `dns_check_use_shell`, `gateway_check_use_shell`, `maintenance_mode_use_shell`.
- If shell syntax is detected without opt-in, a warning is logged and command execution remains `shell=False`.
- Do not embed secrets directly in `command`/dependency command strings; prefer local files or environment wiring that stays outside shared logs.
- When running as **root**, restrict config file permissions (for example `chmod 600` / `root:root`) so webhook URLs and commands are not exposed to other local users.
- On load, if the config file is **group- or world-writable**, a **warning** is logged (unsafe in shared-admin environments).

### Core (this project)

- logical health monitoring while the OS is alive
- external status JSON supervision (generic, shallow interpretation)
- staged recovery policy:
  1. warn
  2. restart target services
  3. reboot only after repeated failures and safeguards

### Optional integration (outside core)

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

## Health Topology Snapshot

Example topology view rendered from `stats.json`:

![raspi-sentinel Health Topology](docs/images/health-topology.png)

- **Python CLI** (`raspi-sentinel` / `python -m raspi_sentinel`)
- **TOML config** as source of truth
- **Rule-based checks per target**:
  - heartbeat file freshness
  - output file freshness
  - command exit status
  - service active status
  - semantic `stats.json` checks (`updated_at`, `last_input_ts`, `last_success_ts`, `records_processed_total`)
  - generic external status JSON checks (`updated_at`, `internal_state`, `last_progress_ts`, `last_success_ts`)
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
   - `>= reboot_threshold`: reboot request (only when `policy_status=failed` and guards allow)

Status model:

- `ok`
- `degraded`
- `failed`

Reason is tracked separately (`healthy`, `clock_frozen`, `clock_jump`, `clock_skewed`, `dns_error`, `gateway_error`, `stats_stale`, ...).
`dns_error` alone does not trigger reboot.

## External Status JSON Watcher (Generic Contract)

`raspi-sentinel` can supervise an external service status file without importing service-specific semantics.

Configured fields (per target):
- `external_status_file`
- `external_status_updated_max_age_sec`
- `external_status_last_progress_max_age_sec`
- `external_status_last_success_max_age_sec`
- `external_status_startup_grace_sec` (default `120`)
- `external_status_unhealthy_values` (default: `["failed", "unhealthy"]`)

Shallow policy inputs:
- `updated_at` stale -> degraded candidate
- `internal_state` in unhealthy set -> unhealthy candidate
- `last_progress_ts` stale -> progress stall candidate
- `last_success_ts` stale -> success stall candidate

Out of policy scope (kept as optional evidence only):
- `reason`
- `recovery`
- `components.*`

Startup behavior:
- When `updated_at` is fresh and startup grace is active, null/empty `last_progress_ts` and `last_success_ts` do not immediately fail the target.

Example contract:

```json
{
  "updated_at": "2026-04-15T12:00:00+00:00",
  "internal_state": "healthy",
  "last_progress_ts": "2026-04-15T11:59:50+00:00",
  "last_success_ts": "2026-04-15T11:59:12+00:00",
  "reason": "optional service-specific detail",
  "recovery": {},
  "components": {}
}
```

## Boundary With App Self-Heal

- Application (for example `amazon-notify`): in-process semantic self-heal (reconnect/backoff/circuit breaker) and status emission.
- `raspi-sentinel`: external supervisor with staged recovery (`warn -> restart -> reboot`) based on generic signals.
- `raspi-sentinel` does not interpret app-specific keys such as Pub/Sub/Gmail/Discord component semantics.

## Escalation Layering

- Prefer short-loop process restart to `systemd` first.
- `raspi-sentinel` uses threshold/cooldown-based escalation on top of that.
- Reboot is additionally guarded and is suppressed immediately after a recent restart (`restart_cooldown_sec`) to avoid restart/reboot storms.

Reboot loop guards:

- minimum uptime before reboot
- reboot cooldown
- max reboot count in rolling window

## Notification Flow (Discord)

1. Incident detected: send `problem`, `action_taken`, `consecutive_failures`
2. Schedule follow-up (default `followup_delay_sec = 300`)
3. On first cycle after due time, send current status (`healthy/unhealthy`)
4. Send periodic heartbeat (`heartbeat_interval_sec`) with uptime/load/disk
5. If delivery fails due to network/transient transport errors:
   - failed notifications are queued in `state.json` (internal backlog)
   - retries are attempted every `retry_interval_sec` (default 60s)
  - retries are aggregated into a single summary message, not one message per failed attempt
  - summary includes failure window:
    - `delivery_failed_from=...`
    - `delivery_failed_until=...`
    - `failed_notifications_total=...`
    - `contexts=...`

## Storage Tiers (SD Wear Optimization)

`raspi-sentinel` can split runtime files into volatile and durable tiers so frequent writes stay on tmpfs while reboot-sensitive state remains on disk.

- docs: `docs/storage-tiers.md`
- config: optional `[storage]` section in `config/raspi-sentinel.example.toml`
- profile examples:
  - `config/examples/production.toml`
  - `config/examples/lightweight-pi.toml`
  - `config/examples/no-discord.toml`
  - `config/examples/tmpfs-tiered.toml`
- verify command: `raspi-sentinel -c /etc/raspi-sentinel/config.toml verify-storage --json`
- `require_tmpfs` default: `false` (opt-in)

## Non-goals

- Full "SD-free operation" is explicitly not supported in this release.
  `events.jsonl` and durable safety state are intentionally kept on disk.
- This feature does not replace system-level durability controls
  (power-loss handling, filesystem checks, hardware watchdog strategy).

## Install

### 1. Install package

Option A: source checkout (current default)

```bash
git clone https://github.com/yukimurata0421/raspi-sentinel.git
cd raspi-sentinel
git config core.hooksPath .githooks
python3 -m pip install .
```

Option B: `pipx` from GitHub (no local clone required)

```bash
pipx install "git+https://github.com/yukimurata0421/raspi-sentinel.git@main"
```

Option C: PyPI (after package publish)

```bash
pipx install raspi-sentinel
# or
python3 -m pip install raspi-sentinel
```

Before push, run:

```bash
bash scripts/prepush_check.sh
```

### 2. Install config

```bash
sudo install -d -m 0755 /etc/raspi-sentinel
sudo install -m 0600 -o root -g root config/raspi-sentinel.example.toml /etc/raspi-sentinel/config.toml
```

Edit `/etc/raspi-sentinel/config.toml` for your real services/paths.

### 3. Prepare state directory

```bash
sudo install -d -m 0755 /var/lib/raspi-sentinel
```

### 4. Install core systemd units

```bash
sudo install -m 0644 systemd/raspi-sentinel.service /etc/systemd/system/raspi-sentinel.service
sudo install -m 0644 systemd/raspi-sentinel.timer /etc/systemd/system/raspi-sentinel.timer
sudo install -m 0644 systemd/raspi-sentinel-tmpfs-verify.service /etc/systemd/system/raspi-sentinel-tmpfs-verify.service
sudo systemctl daemon-reload
sudo systemctl enable --now raspi-sentinel.timer
```

Optional tmpfs mount unit (when `[storage]` uses `/run/raspi-sentinel/*`):

```bash
sudo install -m 0644 systemd/run-raspi\\x2dsentinel.mount /etc/systemd/system/run-raspi\\x2dsentinel.mount
sudo systemctl daemon-reload
sudo systemctl enable --now run-raspi\\x2dsentinel.mount
```

`raspi-sentinel.service` requires `raspi-sentinel-tmpfs-verify.service`.
When tmpfs tiering is enabled and verification fails, service start is blocked.
On low-memory models, cap tmpfs footprint with systemd controls such as
`RuntimeDirectorySize=` (or mount `size=` option) before enabling strict verification.

### 5. Validate dry-run

```bash
sudo raspi-sentinel -c /etc/raspi-sentinel/config.toml --dry-run --verbose run-once
```

### Docker dry-run only (quick-check profile)

Build:

```bash
docker build -f docker/Dockerfile.dryrun -t raspi-sentinel:dryrun .
```

Run (`run-once --dry-run --json` by default):

```bash
docker run --rm \
  -v /etc/raspi-sentinel:/config:ro \
  -v /var/lib/raspi-sentinel:/var/lib/raspi-sentinel \
  raspi-sentinel:dryrun
```

The dry-run image only accepts `run-once` and always enforces `--dry-run`.
`loop` / `verify-storage` are intentionally blocked.

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

Enable JSON logs for collectors/Loki pipelines:

```bash
raspi-sentinel --structured-logging -c /etc/raspi-sentinel/config.toml run-once
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
retry_interval_sec = 60
heartbeat_interval_sec = 0
notify_on_recovery = false
```

Set `heartbeat_interval_sec = 0` to disable periodic healthy-state notifications.
Set `notify_on_recovery = false` to suppress "Recovered" messages.
When notification delivery fails due to network/transient errors, failures are aggregated and retried
every `retry_interval_sec` as one summary message containing the failure window (`from`/`until`).

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
network_probe_enabled = true
network_interface = "wlan0"
gateway_probe_timeout_sec = 2
internet_ip_targets = ["1.1.1.1", "8.8.8.8"]
dns_query_target = "blender.prod.fr24.io"
http_probe_target = "https://www.google.com/generate_204"
time_health_enabled = true
check_interval_threshold_sec = 30
wall_clock_freeze_min_monotonic_sec = 25
wall_clock_freeze_max_wall_advance_sec = 1
wall_clock_drift_threshold_sec = 30
http_time_probe_url = "https://www.google.com"
http_time_probe_timeout_sec = 5
clock_skew_threshold_sec = 300
clock_anomaly_reboot_consecutive = 3

[targets.consecutive_failure_thresholds]
degraded = 2
failed = 6

[targets.latency_thresholds_ms]
gateway = 100
internet_ip = 350
dns = 500
http_total = 1200

[targets.packet_loss_thresholds_pct]
gateway = 20
internet_ip = 30

# optional command-based dependency checks can coexist
# dns_check_command = "getent ahostsv4 blender.prod.fr24.io >/dev/null"
# dns_check_use_shell = true
```

`network_uplink` monitoring layers (evidence):

- `link_ok`: Wi-Fi/L2 availability summary
  - detail evidence: `iface_up`, `wifi_associated`, `ip_assigned`, `operstate_raw`
- `default_route_ok`: default route presence and route metadata (`default_route_iface`, `gateway_ip`)
- `gateway_ok`: local gateway reachability (`gateway_latency_ms`, `gateway_packet_loss_pct`, `neighbor_resolved`, `arp_gateway_ok`)
- `internet_ip_ok`: WAN reachability without DNS (from `internet_ip_targets`)
- `dns_ok`: DNS resolution (`dns_server`, `dns_query_target`, `dns_latency_ms`, `dns_error_kind`)
  - `dns_error_kind`: `nxdomain`, `timeout`, `resolver_config_missing`, `no_server`, `unreachable`, `unknown`
- `http_probe_ok`: upper-layer reachability (`http_status_code`, connect/TLS/total latency, `http_error_kind`)
  - success requires `200 <= http_status_code < 300`
  - probe uses HTTP `HEAD`; choose an endpoint that accepts `HEAD` (or returns `405` intentionally and treat it as probe failure)
  - `http_error_kind`: `dns_resolution_failed`, `connect_timeout`, `read_timeout`, `tls_error`, `connection_refused`, `non_2xx`, `unknown`

Typical reasons and split:

- `link_error`: Wi-Fi/NIC/AP association layer issue
- `route_missing`: default route missing or broken
- `gateway_error`: LAN path to gateway broken
- `wan_error`: gateway reachable but outbound IP path broken
- `dns_error`: outbound IP reachable but DNS fails
- `http_error`: DNS works but HTTP/TLS/upstream fails
- `target_reachability_error`: internet mostly reachable but specific destination fails

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
        "link_ok": true,
        "default_route_ok": true,
        "internet_ip_ok": true,
        "dns_ok": false,
        "gateway_ok": true,
        "dns_latency_ms": 620.1,
        "dns_error_kind": "timeout"
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

Verify tmpfs storage mount/permissions/writability before monitor start:

```bash
raspi-sentinel -c /etc/raspi-sentinel/config.toml verify-storage --json
```

`verify-storage` creates `/run/raspi-sentinel` when missing, then validates mount type,
permissions, writability, and free space.

Operator preflight checks (permissions/timer/tmpfs/threshold sanity):

```bash
raspi-sentinel -c /etc/raspi-sentinel/config.toml doctor --json
```

`doctor --json` includes `network_only_failures_excluded_from_reboot` (expected `true` in default policy).
`network_only_failures_can_reboot` is kept as a compatibility field in `v0.8.x` and is planned for removal in `v1.0.0`.

State introspection (schema version, counters, last actions):

```bash
raspi-sentinel -c /etc/raspi-sentinel/config.toml explain-state --json
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
| `13` | State lock acquisition failed (timeout or lock I/O error) |
| `14` | State persistence failed |
| `15` | `validate-config --strict` found warnings |
| `16` | Storage verification failed (`verify-storage`) |

Use `0` vs `1` / `2` in systemd `ExecStart=` or scripts if you alert on unhealthy cycles or reboot requests.

## `stats.json` vs `events.jsonl`

- `stats.json`: current snapshot ("now"), overwritten atomically.
- `events.jsonl`: append-only transition/history log ("what changed"), written only on status/reason/action changes.
- contract details and schema-version policy: [docs/output-contract.md](docs/output-contract.md)

Example `events.jsonl` lines:

```json
{"ts":"2026-04-10T21:25:00+09:00","service":"network_uplink","from":"ok","to":"degraded","reason":"wan_error","link_ok":true,"default_route_ok":true,"gateway_ok":true,"internet_ip_ok":false,"dns_ok":null,"http_probe_ok":null}
{"ts":"2026-04-10T21:27:00+09:00","service":"network_uplink","from":"degraded","to":"degraded","reason":"wan_error","action":"warn","gateway_ok":true,"internet_ip_ok":false,"internet_fail_consecutive":3}
{"ts":"2026-04-10T21:31:00+09:00","service":"network_uplink","from":"degraded","to":"ok","reason":"healthy","gateway_ok":true,"internet_ip_ok":true}
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

Test ownership map:

- [docs/facts/test-map.md](docs/facts/test-map.md)

New test taxonomy (new files only):

- `tests/unit/`
- `tests/scenario/`
- `tests/e2e/`

Priority scenarios:

- `systemd` NG (`service_active` failure) -> restart path
- `stats.json` update stop (`updated_at` stale) -> `stalled`
- `gateway_ok=true` and `dns_ok=false` (DNS-only) -> no reboot
- `gateway_ok=true` and `internet_ip_ok=false` -> `wan_error`
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
  --cov=raspi_sentinel.cli \
  --cov=raspi_sentinel.config \
  --cov=raspi_sentinel.config_summary \
  --cov=raspi_sentinel.engine \
  --cov=raspi_sentinel.recovery \
  --cov=raspi_sentinel.policy \
  --cov=raspi_sentinel.status_events \
  --cov=raspi_sentinel.time_health \
  --cov-branch \
  --cov-report=term-missing \
  --cov-fail-under=80
python -m coverage report \
  --include="src/raspi_sentinel/policy.py,src/raspi_sentinel/status_events.py,src/raspi_sentinel/cli.py,src/raspi_sentinel/engine.py,src/raspi_sentinel/config_summary.py" \
  --fail-under=85
python -m coverage report \
  --include="src/raspi_sentinel/checks/*.py,src/raspi_sentinel/recovery.py" \
  --fail-under=88
python -m coverage report \
  --include="src/raspi_sentinel/cycle_notifications.py,src/raspi_sentinel/notify.py" \
  --fail-under=90
```

## Versioning

- **Current release line:** **0.8.0** (see `CHANGELOG.md`).
- **Single version string:** `src/raspi_sentinel/_version.py` (`raspi_sentinel.__version__`). `pyproject.toml` reads it at build time (no duplicate number).
- **Git tags:** use `v0.8.0` (or current `__version__`) for releases. An older **`v0.2.0`** tag may exist as a snapshot — details in [docs/VERSIONING.md](docs/VERSIONING.md).

## License

This project is licensed under the MIT License.
See `LICENSE` for details.
