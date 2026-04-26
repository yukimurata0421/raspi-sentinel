# Test Map

This document explains what current tests protect and how the taxonomy is used.

## Current Coverage Map

Tests are organized under three buckets:

- `tests/unit/`
- `tests/scenario/`
- `tests/e2e/`

Representative coverage by behavior:

- module behavior tests: check/result evaluation, config validation/model behavior, monitor snapshot behavior
- policy and state transition tests: classification/escalation behavior, cycle-level transition semantics
- persistence and snapshot tests: state/event persistence paths and snapshot/status serialization
- CLI and packaging smoke tests: entrypoint behavior and version/package contract
- operational tooling tests: deployment helper control flow (`scripts/deploy_pi5_guard.py`) rollback/mode gates

## Taxonomy Usage

- `tests/unit/`: isolated pure logic tests with minimal fixture surface.
- `tests/scenario/`: cross-module behavior and policy transition scenarios.
- `tests/e2e/`: CLI/package/runtime smoke tests that exercise realistic execution paths.

When adding a test, place it directly in one of the three taxonomy directories.
