# Clock Anomaly Detection Policy (Implemented)

This project detects clock anomalies conservatively.
It does not correct system time directly (`date -s` is never executed).

## Status and Reason

- `status`: `ok` / `degraded` / `failed`
- `reason`: `healthy`, `insufficient_interval`, `clock_frozen`, `clock_frozen_persistent`, `clock_frozen_confirmed`, `clock_jump`, `clock_skewed`, `http_error`, `dns_error`, `gateway_error`, `time_sync_broken`, `time_sync_broken_skewed`, `stats_stale`, `recovered_from_clock_jump`, `recovered_from_clock_skew`

## Decision Table

| Rule | Condition | status | reason | Action |
| --- | --- | --- | --- | --- |
| T1 | `delta_monotonic_sec < check_interval_threshold_sec` | `ok` | `insufficient_interval` | no-op |
| T2 | `delta_monotonic_sec >= wall_clock_freeze_min_monotonic_sec` and `delta_wall_sec <= wall_clock_freeze_max_wall_advance_sec` | `degraded` | `clock_frozen` | no reboot |
| T3 | `abs(clock_drift_sec) >= wall_clock_drift_threshold_sec` and not T2 | `degraded` | `clock_jump` | no reboot |
| T4 | `http_probe_ok=true` and `abs(http_time_skew_sec) >= clock_skew_threshold_sec` | `degraded` | `clock_skewed` | no reboot |
| T5 | `http_probe_ok=false` | `degraded` | `http_error` | no reboot |
| T6 | `dns_ok=false` and `internet_ip_ok=true` | `degraded` | `dns_error` | no reboot |
| T7 | `gateway_ok=false` and `link_ok=true` | `degraded` | `gateway_error` | no reboot |
| T8 | `ntp_sync_ok=false` and skew is below threshold | `degraded` | `time_sync_broken` | no reboot |
| T9 | `ntp_sync_ok=false` and T4 | `degraded` | `time_sync_broken_skewed` | no reboot |
| T10 | T2 occurs 2 consecutive times | `degraded` | `clock_frozen_persistent` | still no reboot |
| T11 | T2 occurs `clock_anomaly_reboot_consecutive`+ times and `dns_ok=true`, `gateway_ok=true`, `http_probe_ok=true`, large skew present | `failed` | `clock_frozen_confirmed` | reboot |
| T12 | previous `clock_jump`, now recovered | `ok` | `recovered_from_clock_jump` | no-op |
| T13 | previous skew/failure reason, now skew recovered | `ok` | `recovered_from_clock_skew` | no-op |
| T14 | none of anomaly/dependency failure conditions | `ok` | `healthy` | no-op |
| T15 | `stats.json` stale or unreadable (`semantic_updated_at` / `semantic_stats_file`) | `degraded` | `stats_stale` | service recovery path candidate |

## Reboot Policy

Reboot is allowed only when clock freeze is confirmed by persistence and multi-signal evidence:

1. freeze counter reached configured threshold
2. `dns_ok=true`
3. `gateway_ok=true`
4. `http_probe_ok=true`
5. large external skew is present

`ntp_sync_ok=false` is a strengthening signal, not a standalone reboot trigger.

## events.jsonl

Events are append-only and emitted when status/reason changes or a recovery action runs.

Required fields in emitted events:

- `ts`
- `service`
- `from`
- `to`
- `reason`

Optional evidence fields are added when available:

- `action`
- `delta_wall_sec`
- `delta_monotonic_sec`
- `clock_drift_sec`
- `http_time_skew_sec`
- `dns_ok`
- `gateway_ok`
- `http_probe_ok`
- `ntp_sync_ok`
- `consecutive_clock_freeze_count`
- `stats_age_sec`
