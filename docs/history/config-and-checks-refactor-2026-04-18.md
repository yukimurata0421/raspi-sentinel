# Config/Checks Refactor Memo (2026-04-18)

> Historical note: this memo preserves wording and paths at the time of writing.
> Current repository paths may differ due to later test taxonomy refactors.

## Scope

This memo records the follow-up implementation for:

1. `config.py` size reduction
2. safer observation-key handling in `policy.py`
3. splitting oversized internal-branch tests

## 1) Config Refactor

`src/raspi_sentinel/config.py` was converted into a stable API facade and split into:

- `src/raspi_sentinel/config_models.py`
- `src/raspi_sentinel/config_loader.py`
- `src/raspi_sentinel/config.py` (re-export facade)

### Compatibility

The following imports continue to work from `raspi_sentinel.config`:

- config dataclasses (`AppConfig`, `TargetConfig`, etc.)
- `load_config`
- helper functions used by tests (`_require_int`, `_validate_target_rules`, `_warn_config_permissions`)

This keeps existing call sites and tests stable while reducing file complexity.

## 2) Observation-Key Safety in Policy

`Observations` was already a `TypedDict`, but policy checks still had raw key strings.
To reduce typo risk, typed flag access was introduced:

- `ObservationBooleanFlag` (`Literal[...]`) in `checks/models.py`
- `is_observation_flag_true(observations, key)` helper

`policy.py` now uses this helper for latency/loss threshold flags.

## 3) Test Structure Refactor

`tests/test_checks_internal_branches.py` was split by concern:

- `tests/test_checks_internal_file_command.py`
- `tests/test_checks_internal_network.py`
- `tests/test_checks_internal_stats_and_progress.py`
- `tests/checks_internal_branches_helpers.py` (shared target helper)

No behavior changes were introduced; this is a maintainability split.

## Validation

The following were run successfully after refactor:

- `ruff check src tests`
- `mypy src/raspi_sentinel`
- `pytest -q`
