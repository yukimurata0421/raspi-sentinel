# Architecture

`raspi-sentinel` is a local control loop for Raspberry Pi service recovery.

## Runtime Components

- **CLI orchestrator** (`cli.py`): executes one cycle (`run-once`) or repeated cycles (`loop`)
- **Checks layer** (`checks/` package): process/dependency/semantic checks
- **Time-health layer** (`time_health.py`): monotonic vs wall-clock anomaly detection
- **Policy layer** (`policy.py`): computes `status` and `reason`
- **Recovery layer** (`recovery.py`): decides `warn -> restart -> reboot-request` actions
- **Event/state outputs** (`status_events.py`, `state.py`, `monitor_stats.py`): `events.jsonl`, `state.json`, monitor stats snapshot

## Cycle Flow

1. Load config and persisted state.
2. For each target:
   1. Evaluate maintenance suppression.
   2. Run checks and collect observations.
   3. Apply semantic progress and time-health logic.
   4. Classify policy (`ok` / `degraded` / `failed` + reason).
   5. Apply recovery action with safeguards (reboot is requested, not executed here).
   6. Emit transition events and notifications.
3. Persist state and monitor snapshot.
4. If reboot was requested and persistence succeeded, execute deferred reboot command.

## State Surfaces

- `state.json`: recovery counters and per-target runtime state.
- `events.jsonl`: append-only transition/action events.
- monitor stats (`monitor_stats_file`): aggregate status snapshot for sentinel itself.

## Responsibility Boundary

`raspi-sentinel` is responsible for semantic detection and staged recovery while the OS is alive.

It is not a replacement for:

- hardware watchdog for full kernel hangs
- NTP daemon responsibilities (it observes time-health; it does not set system time)

## Network Uplink Layering

When `network_probe_enabled=true`, `network_uplink` evidence is collected in this order:

1. link (`link_ok`)
2. route (`default_route_ok`)
3. gateway (`gateway_ok`)
4. WAN without DNS (`internet_ip_ok`)
5. DNS (`dns_ok`)
6. HTTP/TLS upper layer (`http_probe_ok`)

Policy evaluates these as "state summary" while preserving raw measurements as evidence.
Single-cycle failures can stay `ok` (`transient_network_failure`), and sustained failures are promoted
to `degraded`/`failed` according to `consecutive_failure_thresholds`.
