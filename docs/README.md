# Documentation Index

This index is the entry point for operational and design documentation of `raspi-sentinel`.

## Core Buckets

- [Facts](facts/README.md): runtime behavior, operational contracts, and data surfaces
- [Principles](principles/README.md): design intent, trust boundaries, and rationale

## Start Here (Operators)

- [Operations Runbook](facts/operations-runbook.md)
- [Data Contracts](facts/data-contracts.md)
- [Output Contract](output-contract.md)
- [Test Map](facts/test-map.md)
- [Exit Codes](facts/exit-codes.md)
- [Time Health Decision Table](time-health-decision-table.md)

## Design Rationale

- [Engineering Decisions](principles/engineering-decisions.md)
- [Recovery Philosophy](principles/recovery-philosophy.md)

## Lifecycle and Release

- [Versioning](VERSIONING.md)
- [Release Notes](release-notes/v0.8.0.md)

## Historical Notes

- [改善提案対応状況 (2026-04-17)](history/improvement-status-2026-04-17.md)
- [レビュー対応メモ (2026-04-18)](history/review-followup-2026-04-18.md)
- [Config/Checks リファクタメモ (2026-04-18)](history/config-and-checks-refactor-2026-04-18.md)

## Optional Integration

- [Watchdog Integration](watchdog.md)
- [Storage Tiers (tmpfs + durable split)](storage-tiers.md)
