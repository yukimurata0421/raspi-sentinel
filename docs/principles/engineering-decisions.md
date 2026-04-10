# Engineering Decisions

This document records why `raspi-sentinel` uses its current architecture and documentation structure.

The format follows a pragmatic rule: **state the decision, point to implementation, explain rationale and tradeoff**.

---

## 0. Documentation Structure

### 0-1. Split docs into `facts/` and `principles/`

Locations: [docs/README.md](../README.md), [docs/facts/README.md](../facts/README.md), [docs/principles/README.md](README.md)

Rationale: Operators and maintainers have different reading needs. Facts document runtime contracts and procedures; principles document intent and tradeoffs. This reduces ambiguity during incidents.

### 0-2. Keep an explicit engineering-decision log

Locations: this file

Rationale: Complex recovery logic decays without decision memory. A decision log prevents accidental regressions to simplistic "if bad then reboot" behavior.

---

## 1. Runtime Control Loop

### 1-1. Keep a single orchestrator cycle in CLI

Locations: [src/raspi_sentinel/cli.py](../../src/raspi_sentinel/cli.py)

Rationale: One cycle owns ordering (checks -> policy -> recovery -> events -> persistence). This avoids inconsistent side effects when adding features.

### 1-2. Separate evaluation from recovery

Locations: [src/raspi_sentinel/cli.py](../../src/raspi_sentinel/cli.py), [src/raspi_sentinel/recovery.py](../../src/raspi_sentinel/recovery.py)

Rationale: Classification and action execution are intentionally separated so policy can evolve without rewriting actuator logic.

### 1-3. Add machine-readable one-cycle output

Locations: [src/raspi_sentinel/cli.py](../../src/raspi_sentinel/cli.py)

Rationale: `run-once --json` makes the decision result consumable by scripts/Ansible/CI without parsing logs.

---

## 2. Configuration and Preflight

### 2-1. Keep strict config validation in `load_config()`

Locations: [src/raspi_sentinel/config.py](../../src/raspi_sentinel/config.py)

Rationale: Reject invalid configurations early (threshold consistency, missing required fields, rule constraints) before runtime side effects happen.

### 2-2. Add `validate-config` as an operator-facing summary

Locations: [src/raspi_sentinel/cli.py](../../src/raspi_sentinel/cli.py), [src/raspi_sentinel/config_summary.py](../../src/raspi_sentinel/config_summary.py)

Rationale: Validation errors alone are not enough; operators need visibility into effective rules, thresholds, shell usage, and path/service existence.

### 2-3. Warn on unsafe config permissions

Locations: [src/raspi_sentinel/config.py](../../src/raspi_sentinel/config.py), [src/raspi_sentinel/config_summary.py](../../src/raspi_sentinel/config_summary.py)

Rationale: Config carries shell commands and optional webhook secrets. Group/world-writable config is a local privilege boundary risk.

---

## 3. State and Mutation Model

### 3-1. Use `TargetState` as mutation boundary

Locations: [src/raspi_sentinel/state_models.py](../../src/raspi_sentinel/state_models.py), [src/raspi_sentinel/recovery.py](../../src/raspi_sentinel/recovery.py), [src/raspi_sentinel/checks.py](../../src/raspi_sentinel/checks.py), [src/raspi_sentinel/time_health.py](../../src/raspi_sentinel/time_health.py)

Rationale: Centralizing mutable per-target fields in a model prevents drift from ad-hoc dict writes and improves maintainability.

### 3-2. Preserve dict identity with `merge_into()`

Locations: [src/raspi_sentinel/state_models.py](../../src/raspi_sentinel/state_models.py)

Rationale: Some callers retain references to target dicts. Merge-back with identity preservation avoids subtle aliasing bugs.

### 3-3. Capture pre-recovery failure counters before evaluation mutation

Locations: [src/raspi_sentinel/cli.py](../../src/raspi_sentinel/cli.py)

Rationale: Notification semantics depend on "previous" counters. Capturing them before in-cycle mutations keeps intent explicit.

---

## 4. Health Classification

### 4-1. Keep `status` and `reason` separate

Locations: [src/raspi_sentinel/policy.py](../../src/raspi_sentinel/policy.py)

Rationale: A small stable status set (`ok`, `degraded`, `failed`) plus detailed reason supports both dashboards and precise incident analysis.

### 4-2. Preserve evidence fields as first-class data

Locations: [src/raspi_sentinel/status_events.py](../../src/raspi_sentinel/status_events.py)

Rationale: Operational decisions must be explainable. Evidence serialization prevents opaque "action happened" narratives.

---

## 5. Time-Health and Dependency Diagnosis

### 5-1. Use monotonic clock as local progression reference

Locations: [src/raspi_sentinel/time_health.py](../../src/raspi_sentinel/time_health.py)

Rationale: `time.monotonic()` is resilient against wall-clock adjustments and is appropriate for progression anomaly detection.

### 5-2. Treat HTTP Date as corroboration, not authoritative sync

Locations: [src/raspi_sentinel/time_health.py](../../src/raspi_sentinel/time_health.py), [docs/time-health-decision-table.md](../time-health-decision-table.md)

Rationale: HTTP `Date` confirms upper-layer reachability and coarse skew, but it is not an NTP replacement.

### 5-3. Require multi-signal persistence before reboot

Locations: [src/raspi_sentinel/time_health.py](../../src/raspi_sentinel/time_health.py), [src/raspi_sentinel/recovery.py](../../src/raspi_sentinel/recovery.py)

Rationale: Reboot is destructive. The system requires repeated freeze evidence and healthy dependency probes before escalating.

### 5-4. Separate DNS vs gateway failure classes

Locations: [src/raspi_sentinel/checks.py](../../src/raspi_sentinel/checks.py), [src/raspi_sentinel/policy.py](../../src/raspi_sentinel/policy.py)

Rationale: DNS failures and path failures have different remediation paths. Mixing them leads to unnecessary reboot behavior.

---

## 6. Observability and Auditability

### 6-1. Keep `events.jsonl` append-only and transition-oriented

Locations: [src/raspi_sentinel/status_events.py](../../src/raspi_sentinel/status_events.py)

Rationale: Append-only transition logs preserve incident timelines while minimizing storage growth.

### 6-2. Export aggregate monitor stats separately from event history

Locations: [src/raspi_sentinel/monitor_stats.py](../../src/raspi_sentinel/monitor_stats.py)

Rationale: Snapshot and history serve different query patterns. Split surfaces keep both cheap and clear.

---

## 7. Safety and Security

### 7-1. Treat shell commands as trusted admin input only

Locations: [README.md](../../README.md), [src/raspi_sentinel/checks.py](../../src/raspi_sentinel/checks.py)

Rationale: Command checks run via shell execution. The trust boundary is local admin ownership, not untrusted multi-tenant input.

### 7-2. Keep direct time correction out of recovery loop

Locations: [src/raspi_sentinel/time_health.py](../../src/raspi_sentinel/time_health.py), [docs/time-health-decision-table.md](../time-health-decision-table.md)

Rationale: Automatic wall-clock mutation has broad side effects; detection and staged recovery are safer defaults.

---

## 8. Quality Gates

### 8-1. Use branch-aware coverage and focused gates for critical modules

Locations: [.github/workflows/ci.yml](../../.github/workflows/ci.yml)

Rationale: Recovery correctness depends on branch behavior, not only statement execution. Focused gates keep critical logic protected.

### 8-2. Add tests for operator-facing outputs, not only internal logic

Locations: [tests/test_cli_behavior.py](../../tests/test_cli_behavior.py), [tests/test_config_summary.py](../../tests/test_config_summary.py)

Rationale: `validate-config` and JSON outputs are operational interfaces and must remain stable.

---

## Cross-Cutting Patterns

- **Evidence-first operations**: decisions are emitted with explicit evidence fields.
- **Conservative escalation**: weak single signals do not trigger destructive actions.
- **Separation of concerns**: config loading, evaluation, policy, recovery, and reporting remain distinct layers.
- **Operator visibility as a feature**: preflight and cycle-level JSON outputs are part of reliability, not optional extras.
