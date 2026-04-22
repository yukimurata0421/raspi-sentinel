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

Rationale: One cycle owns ordering (checks -> policy -> recovery -> notifications/stats -> persistence -> deferred reboot). This avoids inconsistent side effects when adding features.

### 1-2. Separate evaluation from recovery

Locations: [src/raspi_sentinel/cli.py](../../src/raspi_sentinel/cli.py), [src/raspi_sentinel/recovery.py](../../src/raspi_sentinel/recovery.py)

Rationale: Classification and action execution are intentionally separated so policy can evolve without rewriting actuator logic.

### 1-3. Defer reboot command until after durable state persistence

Locations: [src/raspi_sentinel/recovery.py](../../src/raspi_sentinel/recovery.py), [src/raspi_sentinel/engine.py](../../src/raspi_sentinel/engine.py), [src/raspi_sentinel/state.py](../../src/raspi_sentinel/state.py)

Decision:

- `apply_recovery()` records reboot intent and appends reboot history to in-memory state.
- The actual reboot command is executed only in engine deferred phase, after `persist_cycle_outputs()` succeeds.
- If reboot command fails after persistence, cycle returns unhealthy with explicit `reason=reboot_command_failed`.

Rationale: Reboot is irreversible side effect. Persisting reboot history first prevents safeguard undercount caused by process termination between reboot request and state save.

### 1-4. Add machine-readable one-cycle output

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

Locations: [src/raspi_sentinel/state_models.py](../../src/raspi_sentinel/state_models.py), [src/raspi_sentinel/recovery.py](../../src/raspi_sentinel/recovery.py), [src/raspi_sentinel/checks/__init__.py](../../src/raspi_sentinel/checks/__init__.py), [src/raspi_sentinel/time_health.py](../../src/raspi_sentinel/time_health.py)

Rationale: Centralizing mutable per-target fields in a model prevents drift from ad-hoc dict writes and improves maintainability.

### 3-2. Preserve dict identity with `merge_into()`

Locations: [src/raspi_sentinel/state_models.py](../../src/raspi_sentinel/state_models.py)

Rationale: Some callers retain references to target dicts. Merge-back with identity preservation avoids subtle aliasing bugs.

### 3-3. Capture pre-recovery failure counters before evaluation mutation

Locations: [src/raspi_sentinel/cli.py](../../src/raspi_sentinel/cli.py)

Rationale: Notification semantics depend on "previous" counters. Capturing them before in-cycle mutations keeps intent explicit.

### 3-4. Split state into durability tiers instead of moving whole `state.json` to tmpfs

Locations: [src/raspi_sentinel/config_loader.py](../../src/raspi_sentinel/config_loader.py), [src/raspi_sentinel/state.py](../../src/raspi_sentinel/state.py), [docs/storage-tiers.md](../storage-tiers.md)

Decision:

- SD wear concern was identified during `raspi-sentinel` development.
- Instead of placing the full state on tmpfs, state persistence is split by durability requirement:
  - volatile tier (tmpfs candidate): frequent counters/snapshots
  - durable tier (disk): `reboot_history`, `followup_schedule`, `notify_backlog`
  - event history tier (disk): `events.jsonl`
- Durability vs wear tradeoff is selected by config (`[storage]`), not hard-coded.

Rationale: A naive full-tmpfs move can break safety guards designed to survive process/host restarts. If durable safety fields disappear, reboot-loop guard and notification continuity can be silently weakened.

Tradeoff: Tiered state introduces configuration and persistence complexity, but preserves recovery safety semantics while reducing high-frequency disk writes.

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

Locations: [src/raspi_sentinel/checks/__init__.py](../../src/raspi_sentinel/checks/__init__.py), [src/raspi_sentinel/policy.py](../../src/raspi_sentinel/policy.py)

Rationale: DNS failures and path failures have different remediation paths. Mixing them leads to unnecessary reboot behavior.

### 5-5. Layer network_uplink evidence from link to HTTP

Locations: [src/raspi_sentinel/checks/__init__.py](../../src/raspi_sentinel/checks/__init__.py), [src/raspi_sentinel/policy.py](../../src/raspi_sentinel/policy.py), [src/raspi_sentinel/time_health.py](../../src/raspi_sentinel/time_health.py)

Decision:

- Collect network evidence in fixed order:
  - `link_ok`
  - `default_route_ok`
  - `gateway_ok`
  - `internet_ip_ok`
  - `dns_ok`
  - `http_probe_ok`
- Keep status as summary and keep per-layer observations as evidence.

Rationale: This ordering separates Wi-Fi/L2, route, LAN gateway, WAN reachability, DNS, and upper-layer failures so incident response can choose the correct remediation path.

Tradeoff: More fields increase payload size and implementation complexity, but they significantly reduce ambiguity in `reason` and improve auditability.

### 5-6. Use consecutive-failure thresholds to suppress single-sample noise

Locations: [src/raspi_sentinel/time_health.py](../../src/raspi_sentinel/time_health.py), [src/raspi_sentinel/policy.py](../../src/raspi_sentinel/policy.py), [src/raspi_sentinel/config.py](../../src/raspi_sentinel/config.py)

Decision:

- Persist per-layer consecutive failure counters in target runtime state.
- Keep single-cycle failures as `ok` with `transient_network_failure`.
- Escalate to `degraded`/`failed` only after configured threshold breaches.

Rationale: One-shot probe failures are common on small edge hosts. Threshold-based escalation reduces alert flapping and avoids unnecessary restart/reboot actions.

Tradeoff: Detection latency increases by a few cycles, but stability and action quality improve.

### 5-7. Preserve `null` vs `false` semantics in evidence

Locations: [src/raspi_sentinel/status_events.py](../../src/raspi_sentinel/status_events.py), [docs/facts/data-contracts.md](../facts/data-contracts.md)

Decision:

- Serialize unknown/unavailable observations as `null`.
- Serialize explicit negative observations as `false`.
- Do not coerce unknown probe data into failure by default.

Rationale: Operational consumers must distinguish "probe unavailable" from "probe failed". Conflating these states causes false diagnoses and misleading post-incident analysis.

### 5-8. Define HTTP probe success as 2xx only and classify failures explicitly

Locations: [src/raspi_sentinel/checks/__init__.py](../../src/raspi_sentinel/checks/__init__.py), [src/raspi_sentinel/status_events.py](../../src/raspi_sentinel/status_events.py), [src/raspi_sentinel/monitor_stats.py](../../src/raspi_sentinel/monitor_stats.py)

Decision:

- Treat HTTP probe as healthy only when `200 <= status < 300`.
- Preserve `http_status_code` even on non-2xx responses.
- Emit explicit `http_error_kind` values (`dns_resolution_failed`, `connect_timeout`, `read_timeout`, `tls_error`, `connection_refused`, `non_2xx`, `unknown`) for operational triage.

Rationale: Status-line parse success alone is not network/application health. Distinguishing transport and upstream failure classes improves incident routing and avoids misleading "healthy" classification on error pages.

### 5-9. Keep `link_ok` as summary and export link/gateway evidence details

Locations: [src/raspi_sentinel/checks/__init__.py](../../src/raspi_sentinel/checks/__init__.py), [src/raspi_sentinel/status_events.py](../../src/raspi_sentinel/status_events.py), [src/raspi_sentinel/monitor_stats.py](../../src/raspi_sentinel/monitor_stats.py)

Decision:

- Keep `link_ok` as a compact summary signal.
- Export raw/derived evidence fields (`iface_up`, `wifi_associated`, `ip_assigned`, `operstate_raw`) and gateway neighbor evidence (`neighbor_resolved`, `arp_gateway_ok`) to both transition events and monitor stats.

Rationale: Summary-only booleans lose root-cause precision. Keeping evidence next to status preserves auditability without changing state-machine simplicity.

### 5-10. Require `policy_status=failed` for reboot escalation

Locations: [src/raspi_sentinel/recovery.py](../../src/raspi_sentinel/recovery.py), [src/raspi_sentinel/policy.py](../../src/raspi_sentinel/policy.py)

Decision:

- Allow restart logic for persistent non-healthy states as before.
- Gate all reboot paths behind `policy_status == "failed"` in addition to existing safeguards.

Rationale: Reboot is the heaviest action. Binding reboot to explicit policy failure prevents long-lived `degraded` states from escalating to disruptive recovery.

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

Locations: [README.md](../../README.md), [src/raspi_sentinel/checks/__init__.py](../../src/raspi_sentinel/checks/__init__.py)

Rationale: Command checks run via shell execution. The trust boundary is local admin ownership, not untrusted multi-tenant input.

### 7-2. Keep direct time correction out of recovery loop

Locations: [src/raspi_sentinel/time_health.py](../../src/raspi_sentinel/time_health.py), [docs/time-health-decision-table.md](../time-health-decision-table.md)

Rationale: Automatic wall-clock mutation has broad side effects; detection and staged recovery are safer defaults.

### 7-3. Require storage verification before service run when tmpfs tiering is enabled

Locations: [src/raspi_sentinel/storage_verify.py](../../src/raspi_sentinel/storage_verify.py), [systemd/raspi-sentinel.service](../../systemd/raspi-sentinel.service), [systemd/raspi-sentinel-tmpfs-verify.service](../../systemd/raspi-sentinel-tmpfs-verify.service)

Decision:

- `raspi-sentinel.service` now requires `raspi-sentinel-tmpfs-verify.service`.
- `verify-storage` performs:
  1. mount-point / filesystem verification
  2. owner/mode verification
  3. write-read probe
  4. free-capacity check
  5. cooldown wait
- If verification fails under tmpfs tiering, monitor cycle does not start.

Rationale: This catches mount/permission/capacity issues before recovery logic runs and avoids silently writing to an unintended layer.

Cooldown intent:

- allow kernel/mount state to settle after mount activation
- ensure systemd dependency ordering has completed
- create a short human intervention window before monitor actions begin

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
