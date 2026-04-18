# Recovery Philosophy

## Goal

The protected asset is **meaningful data continuity**, not process survival alone.

`systemd` already handles process liveness and restart mechanics.
`raspi-sentinel` adds semantic health and dependency-aware recovery.

## Policy Shape

Recovery is deliberately staged:

1. `warn`
2. `restart`
3. `reboot` (guarded, last resort)

A reboot is never treated as a first response to a single weak signal.

Reboot execution is deferred until `state.json` persistence completes, so reboot-loop safeguards keep accurate history even when the process is terminated by reboot.

## Why DNS/Gateway/Clock Are Split

Operationally, these failures are different classes and need different actions:

- `dns_error`: DNS-only issue; reboot is usually not the first fix.
- `gateway_error`: path-level issue; external checks become less trustworthy.
- clock anomalies (`clock_frozen`, `clock_jump`, `clock_skewed`): require persistence and corroboration.

Mixing these classes leads to noisy and destructive recovery behavior.

## Time-Health Principle

`time.monotonic()` is the local elapsed-time reference.
External HTTP `Date` is a corroborating signal, not a primary time source.
NTP state is diagnostic, not a standalone reboot trigger.

The project does not perform direct time correction commands.

## Evidence-First Operations

Each transition should be explainable from evidence fields, not inferred from action alone.

- `events.jsonl`: immutable transition history
- `run-once --json`: one-cycle machine-readable report
- `validate-config --json`: preflight visibility of what is actually enabled

This keeps incident review reproducible and auditable.
