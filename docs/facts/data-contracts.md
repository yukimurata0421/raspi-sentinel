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
- includes `state_schema_version` for compatibility tracking

Current notification-related internal fields (under `notify`):

- `last_heartbeat_ts`
- `retry_due_ts` (next retry time for deferred delivery summary)
- `delivery_backlog`:
  - `first_failed_ts`
  - `last_failed_ts`
  - `total_failures`
  - `contexts` (`context -> failure_count`)

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
- optional:
  - action/time evidence: `action`, `delta_wall_sec`, `delta_monotonic_sec`, `clock_drift_sec`, `http_time_skew_sec`, `ntp_sync_ok`, `consecutive_clock_freeze_count`, `stats_age_sec`
  - layered network bools: `link_ok`, `iface_up`, `wifi_associated`, `ip_assigned`, `default_route_ok`, `gateway_ok`, `neighbor_resolved`, `arp_gateway_ok`, `internet_ip_ok`, `dns_ok`, `http_probe_ok`, `wan_vs_target_ok`, `dns_server_reachable`
  - network quality/details: `gateway_latency_ms`, `gateway_packet_loss_pct`, `internet_ip_latency_ms`, `internet_ip_packet_loss_pct`, `dns_latency_ms`, `http_total_latency_ms`, `http_connect_latency_ms`, `http_tls_latency_ms`, `http_status_code`
  - link/route metadata: `network_interface`, `operstate_raw`, `ssid`, `bssid`, `rssi_dbm`, `tx_bitrate_mbps`, `rx_bitrate_mbps`, `default_route_iface`, `gateway_ip`, `route_table_snapshot`
  - diagnostic context: `dns_server`, `dns_query_target`, `dns_error_kind`, `http_probe_target`, `http_error_kind`, per-layer consecutive counters
    - `dns_error_kind`: `nxdomain`, `timeout`, `resolver_config_missing`, `no_server`, `unreachable`, `unknown`
    - `http_error_kind`: `dns_resolution_failed`, `connect_timeout`, `read_timeout`, `tls_error`, `connection_refused`, `non_2xx`, `unknown`
  - notification delivery events:
    - `kind: notify_delivery_failed`
    - `context` (for example `issue_notification:<target>`, `followup:<target>`, `periodic_heartbeat`, `deferred_notification_batch`)

Timestamp display notes:

- `events.jsonl.ts` is epoch seconds (numeric, timezone-neutral).
- Human-readable ISO timestamps in notifications/events currently use host local timezone.
- Cross-host consumers should normalize to UTC at ingestion time.

Null semantics:

- `null` means observation unavailable/unknown for that cycle.
- `false` means observed negative result.
- Consumers must not treat `null` and `false` as equivalent.

## 3. monitor stats snapshot (`monitor_stats_file`)

Purpose:

- current aggregate health of sentinel itself
- quick dashboard view (`targets_ok`, `targets_degraded`, `targets_failed`)
- includes `stats_schema_version` for compatibility tracking

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

## 6. Compatibility policy

- output compatibility and schema-version rules are defined in [../output-contract.md](../output-contract.md)
