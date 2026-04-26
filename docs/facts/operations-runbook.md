# Operations Runbook

This document describes practical operator workflows before and during production operation.

## 1. Preflight Before Enabling systemd

1. Validate config and effective rules:

```bash
raspi-sentinel -c /etc/raspi-sentinel/config.toml validate-config
```

2. Optional machine-readable validation for automation:

```bash
raspi-sentinel -c /etc/raspi-sentinel/config.toml validate-config --json
```

3. Enforce warning-free config in automation:

```bash
raspi-sentinel -c /etc/raspi-sentinel/config.toml validate-config --strict
```

4. Dry-run one cycle:

```bash
raspi-sentinel -c /etc/raspi-sentinel/config.toml --dry-run --verbose run-once
```

5. Optional one-cycle JSON output for scripts:

```bash
raspi-sentinel -c /etc/raspi-sentinel/config.toml --dry-run run-once --json
```

`run-once --json` executes normal cycle side effects (recovery/event/state writes) in addition to JSON output.

## 2. Enable Timed Execution

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now raspi-sentinel.timer
```

## 3. Routine Checks

- Recent service logs:

```bash
journalctl -u raspi-sentinel.service -n 200 --no-pager
```

- Recent timer logs:

```bash
journalctl -u raspi-sentinel.timer -n 200 --no-pager
```

- Current monitor snapshot:

```bash
cat /var/lib/raspi-sentinel/stats.json
```

- Recent transition events:

```bash
tail -n 50 /var/lib/raspi-sentinel/events.jsonl
```

## 4. Incident Interpretation

Typical interpretation order:

1. Check `reason` in events and run-once JSON.
2. Inspect layered network evidence in order:
   `link_ok` -> `default_route_ok` -> `gateway_ok` -> `internet_ip_ok` -> `dns_ok` -> `http_probe_ok`.
   For `link_ok`, always check decomposition evidence:
   `iface_up`, `wifi_associated`, `ip_assigned`, `operstate_raw`.
   For gateway path diagnosis, also inspect:
   `neighbor_resolved`, `arp_gateway_ok`, `gateway_ip`, `default_route_iface`.
3. Inspect quality fields when available (`*_latency_ms`, `*_packet_loss_pct`, `rssi_dbm`).
4. Inspect clock fields (`http_time_skew_sec`, `delta_*`, `clock_drift_sec`) for time-health context.
5. Confirm whether action was `warn`, `restart`, or `reboot`.
6. Correlate with journald output of monitored services.

Reason quick map:

- `link_error`: Wi-Fi/NIC/AP association issue
- `route_missing`: default route missing or broken
- `gateway_error`: LAN path to gateway failing
- `wan_error`: gateway reachable but upstream internet IP path failing
- `dns_error`: upstream reachable but DNS failing
- `http_error`: DNS works but HTTP/TLS or upper-layer endpoint fails
- `target_reachability_error`: specific destination issue while general internet can be healthy
- `transient_network_failure`: below consecutive threshold; watch for persistence

HTTP probe notes:

- `http_probe_ok=true` only means 2xx response.
- non-2xx response sets `http_probe_ok=false`, `http_error_kind=non_2xx`.
- `http_error_kind` may be one of:
  `dns_resolution_failed`, `connect_timeout`, `read_timeout`, `tls_error`, `connection_refused`, `non_2xx`, `unknown`.

## 5. Safety Notes

- Do not treat a single external skew observation as reboot evidence.
- Reboot escalation requires `policy_status=failed`; persistent `degraded` alone should not reboot.
- Do not assume `http_error` means local clock failure.
- Keep config file permissions strict (`chmod 600` recommended on production hosts).
- Treat command fields as trusted admin input only.
- Use `*_use_shell=true` only where shell syntax is required.
- If `state.json` is corrupt, the cycle runs in limited mode and records `state_corrupted` event.

## 6. Notification Delivery Outage Runbook

When Discord delivery fails due to network/transient transport failures, raspi-sentinel
does not drop notifications immediately.

Behavior:

1. Failed sends are recorded as `kind=notify_delivery_failed` in `events.jsonl`.
2. Network-related failures are aggregated in `state.json.notify.delivery_backlog`.
3. Retry is attempted every `notify.discord.retry_interval_sec` (default 60 seconds).
4. On recovery, one summary notification is sent with outage window and count.

Quick checks:

```bash
# recent notification delivery failures
grep 'notify_delivery_failed' /var/lib/raspi-sentinel/events.jsonl | tail -n 20

# inspect current retry backlog in state.json
python3 - <<'PY'
import json
from pathlib import Path
p = Path('/var/lib/raspi-sentinel/state.json')
if p.exists():
    s = json.loads(p.read_text())
    n = s.get('notify', {})
    print(json.dumps({
        'retry_due_ts': n.get('retry_due_ts'),
        'delivery_backlog': n.get('delivery_backlog'),
    }, ensure_ascii=False, indent=2))
else:
    print('state.json not found')
PY
```

Interpretation:

- `delivery_backlog.first_failed_ts`: first failed notification timestamp in current outage window.
- `delivery_backlog.last_failed_ts`: last observed failed timestamp in current outage window.
- `delivery_backlog.total_failures`: number of failed notification attempts in the backlog.
- `delivery_backlog.contexts`: failed notification contexts and counts (`issue_notification:*`, `followup:*`, etc).
- `retry_due_ts`: next retry schedule.

If backlog never clears:

1. Check DNS/connectivity from host to Discord webhook endpoint.
2. Check journald for transport details:
   `journalctl -u raspi-sentinel.service -n 200 --no-pager`.
3. Confirm `notify.discord.webhook_url` validity and outbound firewall policy.

## 7. Controlled Deployment to pi5-guard (Pi Zero)

Use controlled staged deployment instead of direct overwrite when updating `/opt/raspi-sentinel`:

```bash
python3 scripts/deploy_pi5_guard.py --host pi5-guard@pi5-guard --mode safe
```

Flow (`--mode safe`):

1. preflight (`ssh`, `sudo -n`, expected paths/config)
2. stage sync to remote staging directory
3. staged validation (`validate-config`, `--dry-run run-once --json`)
4. switch with rollback backup (`/opt/raspi-sentinel.rollback.<timestamp>`)
5. post-deploy health gate (`validate-config`, dry-run/live `run-once --json`)
6. automatic rollback on failure

Useful options:

- `--mode fast`: skips staged validation, keeps switch + post-deploy health gate
- `--dry-run`: prints commands without executing

This deployment helper is intended for operator-controlled host updates and does not create git tags.

## 8. Prometheus Textfile Export

Write one-shot metrics for node_exporter textfile collector:

```bash
raspi-sentinel -c /etc/raspi-sentinel/config.toml export-prometheus \
  --textfile-path /var/lib/node_exporter/textfile_collector/raspi_sentinel.prom
```

Current metrics include:

- config permission health
- threshold consistency
- tmpfs verify status
- timer active status
- state limited-mode/reboot/followup counters

## 9. Permission Repair from Doctor

When config ownership/mode drift is detected:

```bash
sudo raspi-sentinel -c /etc/raspi-sentinel/config.toml doctor --json --fix-permissions
```

Preview only:

```bash
raspi-sentinel -c /etc/raspi-sentinel/config.toml doctor --json --fix-permissions --fix-permissions-dry-run
```

## 10. Failure Injection (Sample)

Use helper script for controlled test scenarios:

```bash
# stop monitored service
sudo python3 scripts/failure_inject.py service-down --service demo.service

# inject stale file (mtime in the past)
python3 scripts/failure_inject.py stale-file --path /tmp/heartbeat.txt --age-sec 900

# restore service
sudo python3 scripts/failure_inject.py service-restore --service demo.service
```
