# Changelog

All notable changes to this project are documented in this file.

Release process and version policy: [docs/VERSIONING.md](docs/VERSIONING.md).

## [0.4.0] - Unreleased

### Added

- CLI subcommand: `validate-config`
  - validates config loadability via existing `load_config()`
  - prints per-target enabled rule summary, effective thresholds, time-health/maintenance settings
  - lists targets that use shell commands
  - supports JSON output (`validate-config --json`) for automation
- CLI option: `run-once --json` to emit one-cycle machine-readable evaluation output (`overall_status`, per-target `status/reason/action/evidence`).
- `config_summary.py` helper module for operator-facing config diagnostics, including optional service-unit/path checks and config-permission warnings.
- Documentation set under `docs/` reorganized into `facts/` and `principles/`, including `docs/principles/engineering-decisions.md`.
- `validate-config --strict` (non-zero exit on warnings) for automation/CI preflight enforcement.
- New global config controls:
  - `events_backup_generations`
  - `state_max_file_bytes`
  - `state_reboots_max_entries`
  - `state_lock_timeout_sec`

### Changed

- `_run_cycle` now captures `previous_failures` before `evaluate_target()` mutation points for clearer intent.
- `apply_records_progress_check()` now uses `TargetState` model mutation (`from_dict` + `merge_into`) instead of direct raw-dict writes.
- `TargetState` now also models clock-related runtime fields:
  - `clock_prev_wall_time_epoch`
  - `clock_prev_monotonic_sec`
  - `consecutive_clock_freeze_count`
  - `clock_anomaly_consecutive`
  - `clock_last_reason`
- `apply_time_health_checks()` now updates target runtime state via `TargetState` end-to-end.
- `state_models` numeric coercion helpers were unified with `state_helpers` (`safe_float`, `safe_optional_int`).
- `status_events` evidence builder is now reusable (`build_event_evidence`) and used by JSON cycle output.
- README expanded with:
  - `validate-config` examples
  - `run-once --json` examples
  - `stats.json` vs `events.jsonl` role separation
  - explicit guarantees / non-guarantees
- State store now uses an exclusive lock to prevent concurrent read-modify-write lost updates.
- `state.json` persistence now has explicit success/failure handling and size guard checks.
- `events.jsonl` rotation now supports multiple backup generations (not only `.1`).
- Time-health check now supports injected monotonic timestamp (`now_mono_ts`) for testability/DI.
- Discord webhook retry logic now respects HTTP `429` `Retry-After` when present.

### Testing

- Added CLI tests for:
  - `run-once --json` output shape
  - `validate-config --json` summary content
  - `validate-config --strict` non-zero behavior
  - end-to-end unhealthy cycle integration (`_run_cycle_collect`: degrade -> restart -> event/state persistence)
- Added config-summary tests for:
  - config permission warning detection
  - formatted summary output for shell-command targets
- Added `TargetState` round-trip and `merge_into()` tests covering new clock fields.
- Added branch tests for `apply_records_progress_check()` model-based behavior (missing/stalled/drop cases).
- Added state persistence/rotation tests:
  - `state.json` size guard and reboot-list trimming
  - multi-generation events rotation behavior

## [0.3.1] - 2026-04-10

### Added

- `PROCESS_CHECK_NAMES` (`policy.py`) for the “process error” branch; clearer than a long `or` chain.
- Expanded `tests/test_policy.py` (clock freeze confirmed, HTTP probe failed, time sync broken skew, recovered-from-clock-skew, process_error).
- CI: **Ruff** (`ruff check`, `ruff format --check`); pytest coverage includes `policy`, `status_events`, `time_health`; separate **coverage gates** for policy+status (≥85%) and checks+recovery (≥88%).

### Changed

- **Recovery** uses `TargetState` end-to-end: load with `TargetState.from_dict`, mutate fields, **`merge_into(raw_dict)`** on the live target dict (no mixed dict/get vs model).
- **`TargetState`**: `last_records_processed_total`, `records_stalled_cycles`, and **`merge_into()`**; progress-check fields round-trip with semantic stats stall logic.
- **`apply_records_progress_check`**: moved from `monitor_stats.py` to **`checks.py`** (same evaluation cycle as other checks).
- **`state_helpers`**: `target_state()` lives here; **`runtime_state.py` removed** (callers import `state_helpers` directly).
- **`state.py`**: empty-state returns use **`copy.deepcopy(DEFAULT_STATE)`** instead of repeated literals.
- **`run-once` exit code**: returns **`1`** if any target is unhealthy this cycle and **`2`** if a reboot was requested (no longer mapped to **`0`**); see README.
- README: **exit code** table; Tests/CI commands aligned with the workflow.

### Removed

- Unused `cli._classify_target_status` / `_classify_target_reason` wrappers (tests use `status_events` directly).

## [0.3.0] - 2026-04-10

### Added

- **`policy` module**: `PolicySnapshot` and `classify_target_policy()` as the single implementation of semantic status (`ok` / `degraded` / `failed`) and `reason`.
- **`src/raspi_sentinel/_version.py`**: single source of truth for `__version__`; `pyproject.toml` uses dynamic version from this attribute.
- **`docs/VERSIONING.md`**: versioning policy, relation to git tags, and release checklist.
- Config: `events_max_file_bytes` for size-based rotation of `events.jsonl` (default 5 MiB; `0` disables).
- Config validation: `service_active = true` requires at least one entry in `services`.
- Config load warning when the config file is group/world-writable.
- `state_helpers` (`safe_*`, atomic JSON write, optional events file rotation).
- `state_models.TargetState` as a typed view for per-target state (recovery uses full merge-back in **0.3.1**).
- Discord webhook retries (limited attempts with backoff) and `notify_delivery_failed` events in `events.jsonl` when delivery fails.
- Tests for policy, classification, and recovery time injection.

### Changed

- **Policy alignment**: `apply_policy_to_result(result, policy: PolicySnapshot)`; `result.healthy` follows policy `is_ok`; notifications use `policy_reason` in messages when present.
- **Time injection**: `run_checks(..., now_wall_ts=...)` and `apply_recovery(..., now_ts=...)` use one wall-clock value per cycle for consistent ages and recovery timestamps (tests use explicit `now_ts` instead of monkeypatching private clocks).
- README: security model (`shell=True`, trusted config, permissions).
- `status_events` delegates classification to `policy`; duplicate classification logic removed.
- User-Agent strings for HTTP clients derive from `__version__` (no hard-coded `0.1`).

### Notes

- A git tag **`v0.2.0`** may exist from an earlier snapshot; there was no formal GitHub Release / PyPI release aligned with that tag. **0.3.0** is the first version where packaging metadata, changelog, and runtime version strings are aligned for distribution. Use tag **`v0.3.0`** for the next release.

## [0.2.0] - 2026-04-10 (snapshot only; superseded by 0.3.0)

Content below reflects the feature set accumulated up to the `v0.2.0` tag; for releases, prefer **0.3.0** and [docs/VERSIONING.md](docs/VERSIONING.md).

### Added

- Clock anomaly monitoring based on `time.time()` vs `time.monotonic()` progression.
- Optional HTTP `Date` header probe and skew observation (`http_time_skew_sec`).
- NTP sync state observation via `timedatectl` (`ntp_sync_ok`).
- `clock_anomaly_consecutive` and `clock_reboot_ready` signals for staged recovery.
- Explicit freeze persistence signal: `consecutive_clock_freeze_count`.
- `CHANGELOG.md` for versioned change tracking.
- MIT `LICENSE` file and README license section.

### Changed

- Refactored CLI and monitoring responsibilities into smaller modules:
  - `runtime_state.py`
  - `maintenance.py`
  - `status_events.py`
  - `monitor_stats.py`
  - `cycle_notifications.py`
  - `time_health.py`
- Recovery policy now blocks clock-only reboot unless anomaly is persistent and confirmation signals are satisfied.
- Recovery policy now allows reboot only for `clock_frozen_confirmed` (persistent freeze + dependency and HTTP confirmation), not for single-sample skew.
- Target health model changed to explicit `status` and `reason` separation.
- Target status vocabulary unified to `ok` / `degraded` / `failed`.
- `events.jsonl` now records transition-oriented entries with required `from` / `to` / `reason`, and optional evidence fields (`delta_*`, `clock_drift_sec`, `http_time_skew_sec`, `dns_ok`, `gateway_ok`, `http_probe_ok`, `ntp_sync_ok`, `consecutive_clock_freeze_count`, `stats_age_sec`).
- Target config schema extended for time-health controls:
  - `time_health_enabled`
  - `check_interval_threshold_sec`
  - `wall_clock_freeze_min_monotonic_sec`
  - `wall_clock_freeze_max_wall_advance_sec`
  - `wall_clock_drift_threshold_sec`
  - `http_time_probe_url`
  - `http_time_probe_timeout_sec`
  - `clock_skew_threshold_sec`
  - `clock_anomaly_reboot_consecutive`
- Monitor snapshot model updated to aggregate by `targets_ok`, `targets_degraded`, `targets_failed` and include per-target `reason`.
- Monitor snapshot includes clock-related fields per target (`clock_reason`, `clock_anomaly_consecutive`, optional `http_time_skew_sec`, optional `ntp_sync_ok`).
- README and example config updated to document semantic/dependency/time-health behavior and operations.
- Added implementation policy document: `docs/time-health-decision-table.md`.

### Testing

- Added clock anomaly tests and recovery-branch tests.
- Added coverage for HTTP Date parsing/error branches, NTP query branches, confirmed clock-freeze reboot gating, and new configuration validation (`check_interval_threshold_sec`).
- CI/coverage expectations kept aligned with policy-driven checks and recovery logic.

## [0.1.0] - Baseline (initial public layout)

### Added

- Initial standalone `raspi-sentinel` implementation for Raspberry Pi service self-healing.
- Rule-based checks for service liveness, heartbeat/output freshness, command checks, semantic stats, and dependency checks (DNS/gateway).
- Staged recovery flow (`warn -> restart -> reboot`) with cooldown and reboot-window safeguards.
- JSON state store, transition events log, and aggregate monitor stats snapshot.
- Systemd timer/service templates and operational documentation.
