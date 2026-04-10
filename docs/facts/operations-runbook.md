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
2. Inspect evidence fields (`dns_ok`, `gateway_ok`, `http_probe_ok`, `http_time_skew_sec`, `delta_*`).
3. Confirm whether action was `warn`, `restart`, or `reboot`.
4. Correlate with journald output of monitored services.

## 5. Safety Notes

- Do not treat a single external skew observation as reboot evidence.
- Do not assume `http_probe_failed` means local clock failure.
- Keep config file permissions strict (`chmod 600` recommended on production hosts).
- Treat command fields as trusted admin input only.
