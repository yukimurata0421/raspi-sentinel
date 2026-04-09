# Changelog

All notable changes to this project are documented in this file.

## [0.2.0] - 2026-04-10 (Unreleased)

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

## [0.1.0] - Baseline (Current GitHub state)

### Added
- Initial standalone `raspi-sentinel` implementation for Raspberry Pi service self-healing.
- Rule-based checks for service liveness, heartbeat/output freshness, command checks, semantic stats, and dependency checks (DNS/gateway).
- Staged recovery flow (`warn -> restart -> reboot`) with cooldown and reboot-window safeguards.
- JSON state store, transition events log, and aggregate monitor stats snapshot.
- Systemd timer/service templates and operational documentation.
