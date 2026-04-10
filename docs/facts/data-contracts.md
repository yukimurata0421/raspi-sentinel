# Data Contracts

This document defines runtime output surfaces and their intended usage.

## 1. `state.json` (internal control state)

Purpose:

- per-target recovery counters (`consecutive_failures`)
- last action/status/reason for transition detection
- clock/progress runtime memory used for next-cycle evaluation

Notes:

- internal control-plane state
- schema may evolve across releases
- should not be used as the primary external observability API

## 2. `events.jsonl` (append-only transition log)

Purpose:

- immutable audit log of status/reason transitions
- records recovery actions (`restart`, `reboot`)
- stores compact evidence relevant to the transition

Write policy:

- append-only
- write on transition/action changes, not every loop

Common fields:

- required: `ts`, `service`, `from`, `to`, `reason`
- optional: `action`, `delta_wall_sec`, `delta_monotonic_sec`, `clock_drift_sec`, `http_time_skew_sec`, `dns_ok`, `gateway_ok`, `http_probe_ok`, `ntp_sync_ok`, `consecutive_clock_freeze_count`, `stats_age_sec`

## 3. monitor stats snapshot (`monitor_stats_file`)

Purpose:

- current aggregate health of sentinel itself
- quick dashboard view (`targets_ok`, `targets_degraded`, `targets_failed`)

Write policy:

- atomic JSON write
- periodically and on state change

## 4. `run-once --json` (one-cycle execution report)

Purpose:

- machine-readable result for a single execution
- automation-friendly output for Ansible/scripts/CI diagnostics

Shape:

- top-level: `updated_at`, `overall_status`, `dry_run`, `reboot_requested`, `targets`
- per target: `status`, `reason`, `action`, `healthy`, `evidence`, optional `failures`

## 5. `validate-config --json` (configuration report)

Purpose:

- operator preflight visibility before enabling timer/service
- lists effective target rules and risk indicators

Includes:

- global config summary
- per-target enabled rules and thresholds
- time-health and maintenance settings
- shell command usage
- path existence checks
- config permission warning
