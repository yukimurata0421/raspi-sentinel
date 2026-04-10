# Architecture

`raspi-sentinel` is a local control loop for Raspberry Pi service recovery.

## Runtime Components

- **CLI orchestrator** (`cli.py`): executes one cycle (`run-once`) or repeated cycles (`loop`)
- **Checks layer** (`checks.py`): process/dependency/semantic checks
- **Time-health layer** (`time_health.py`): monotonic vs wall-clock anomaly detection
- **Policy layer** (`policy.py`): computes `status` and `reason`
- **Recovery layer** (`recovery.py`): applies `warn -> restart -> reboot` actions
- **Event/state outputs** (`status_events.py`, `state.py`, `monitor_stats.py`): `events.jsonl`, `state.json`, monitor stats snapshot

## Cycle Flow

1. Load config and persisted state.
2. For each target:
   1. Evaluate maintenance suppression.
   2. Run checks and collect observations.
   3. Apply semantic progress and time-health logic.
   4. Classify policy (`ok` / `degraded` / `failed` + reason).
   5. Apply recovery action with safeguards.
   6. Emit transition events and notifications.
3. Persist state and monitor snapshot.

## State Surfaces

- `state.json`: recovery counters and per-target runtime state.
- `events.jsonl`: append-only transition/action events.
- monitor stats (`monitor_stats_file`): aggregate status snapshot for sentinel itself.

## Responsibility Boundary

`raspi-sentinel` is responsible for semantic detection and staged recovery while the OS is alive.

It is not a replacement for:

- hardware watchdog for full kernel hangs
- NTP daemon responsibilities (it observes time-health; it does not set system time)
