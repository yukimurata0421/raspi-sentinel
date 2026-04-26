# Output Contract

This document defines compatibility expectations for files emitted by `raspi-sentinel`.

## Scope

- `stats.json`: aggregate status snapshot for operators/UI.
- `state.json` (or tiered state files): runtime recovery state and counters.
- `events.jsonl`: append-only transition/action event stream.

These outputs are treated as semi-public APIs for tooling and integrations.

## Versioned Fields

- `stats.json` MUST include `stats_schema_version` (integer).
- `state.json` MUST include `state_schema_version` (integer).
- Current versions:
  - `stats_schema_version = 1`
  - `state_schema_version = 1`

## Status Compatibility

- Target and overall status values are constrained to:
  - `ok`
  - `degraded`
  - `failed`
  - `unknown`
- Adding a new status value is a breaking change.

## Time Format

- `updated_at` and `events.jsonl.ts` MUST be timezone-aware ISO-8601 strings.
- Producer MUST avoid changing timezone-awareness semantics across versions.

## Reason/Action Compatibility Rules

- `reason` MAY be extended, but existing reason meanings must remain stable.
- New reasons MUST be added with:
  - policy mapping (`reason -> policy_status`)
  - recovery-action expectation (`allowed restart/reboot`)
  - compatibility test fixture update.
- `events.jsonl.kind` additions MUST be append-only and backward compatible.

## JSON Schema

- `docs/schemas/stats.schema.json`
- `docs/schemas/state.schema.json`

These schemas are compatibility guardrails for `v0.8.x` contract hardening.

## Forward Schema Handling

- If a reader sees `*_schema_version` higher than supported, it should keep best-effort parsing
  and emit an operator-visible warning.
- `doctor` currently follows this policy:
  - keeps `last_run_result` as `unknown` for unsupported status values
  - exposes `last_run_stats_schema_version` for debugging
  - logs warning when `stats_schema_version` is newer than supported.

## External Status Producer Contract

For `external_status_file` producers:

- `last_progress_ts` means business/control-plane progress, not timer heartbeat.
- `last_success_ts` means successful completion of user-meaningful work.
- Producers should document their own `progress`/`success` definitions.
