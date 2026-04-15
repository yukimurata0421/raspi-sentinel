# AGENTS.md

## Scope
- This file governs work under `/home/yuki/projects/raspi-sentinel`.
- Treat this directory as the development source of truth.

## Repo Strategy
- Canonical development repo: `/home/yuki/projects/raspi-sentinel`
- Public mirror repo: `/home/yuki/publish/raspi-sentinel`
- Default flow: implement and verify in development repo first, then sync to publish repo.

## Engineering Bar (Senior)
- Optimize for operational safety first, then type safety, then implementation speed.
- Prefer fail-closed behavior for risky paths (reboot, restart, shell execution, state mutation).
- Do not merge "works in happy path only" changes.
- Any change that alters recovery/state/notify behavior must include boundary tests.
- Keep behavior explicit and observable (clear return codes, structured JSON status, event logs).

## Product Intent (Do Not Drift)
- Primary goal is meaningful data continuity, not process survival alone.
- Role split:
  - `systemd`: process liveness.
  - `raspi-sentinel`: semantic health and dependency-aware recovery.
- Treat this project as a low-storage semantic health monitor with dependency-aware recovery, not a generic watchdog.

## Non-Negotiable Safety Rules
- No destructive git/file operations (`reset --hard`, force checkout, deleting unrelated changes).
- Never discard existing user changes unless explicitly requested.
- Keep limited mode as a hard guardrail: no disruptive actions when state is corrupted/unreliable.
- Shell execution must remain explicit opt-in (`*_use_shell=true`) and fail closed otherwise.
- Never auto-correct system time (`date -s` or equivalent) from sentinel logic.

## Time-Health Policy Guardrails
- Use `time.monotonic()` as primary elapsed-time reference.
- Use HTTP `Date` as secondary corroboration (`http_probe_ok`, `http_time_skew_sec`), not authoritative time sync.
- A single skew observation (for example local 10:00 vs external 10:05) must not trigger immediate reboot.
- Reboot only when persistence and multi-signal confirmation criteria are satisfied.
- Keep DNS / gateway / clock causes separated in classification and recovery decisions.

## State, Events, and Observability Model
- `stats.json` is current snapshot; `events.jsonl` is transition/event log; journald is detail log.
- State corruption must be explicit:
  - quarantine invalid state file
  - emit state corruption/load error event
  - continue in limited mode (no disruptive actions)
- Preserve machine-readable outputs and stable exit codes for automation.

## Required Local Verification (Before Commit)
- `./.venv/bin/ruff check .`
- `./.venv/bin/mypy --strict src/raspi_sentinel`
- `./.venv/bin/pytest`
- CI-equivalent coverage gate:
  - `./.venv/bin/pytest --cov=raspi_sentinel.checks --cov=raspi_sentinel.cli --cov=raspi_sentinel.config --cov=raspi_sentinel.config_summary --cov=raspi_sentinel.recovery --cov=raspi_sentinel.policy --cov=raspi_sentinel.status_events --cov=raspi_sentinel.time_health --cov-branch --cov-report=term-missing --cov-report=xml --cov-fail-under=80`
- Additional gates:
  - `./.venv/bin/python -m coverage report --include='src/raspi_sentinel/policy.py,src/raspi_sentinel/status_events.py' --fail-under=85`
  - `./.venv/bin/python -m coverage report --include='src/raspi_sentinel/checks.py,src/raspi_sentinel/recovery.py' --fail-under=88`

## Operational Boundary Test Policy
- Add or update tests for these scenarios whenever related code changes:
  - corrupted state + limited mode + notify interactions
  - reboot guard boundaries (uptime, cooldown, window cap)
  - shell opt-in misconfiguration behavior in real cycle paths
  - lock timeout behavior from timer/service perspective (return code and machine-readable report)
  - backward compatibility of state migration (`GlobalState.from_dict` and round-trip)

## Change Scope Discipline
- Keep patches small and reviewable; avoid broad refactors unless required.
- Separate mechanical changes from behavioral changes when possible.
- Preserve backward compatibility for persisted state formats and CLI contracts.
- For architecture changes, keep `cli.py` focused on argument parsing/dispatch; move orchestration logic into engine modules.

## Commit and Push Practice
- Commit message style: `<type>: <concise intent>` (for example, `fix: ...`, `test: ...`, `feat: ...`).
- Include only relevant files in each commit.
- After passing verification, push the branch used for development.

## Sync to Publish Repo
- Use non-destructive sync from development to publish after validation.
- Recommended command:
  - `rsync -a --exclude='.git' --exclude='.venv' --exclude='.mypy_cache' --exclude='.pytest_cache' --exclude='.ruff_cache' /home/yuki/projects/raspi-sentinel/ /home/yuki/publish/raspi-sentinel/`

## Reporting Format (for humans and automation)
- Always report:
  - what changed
  - commit SHA
  - verification commands and results
  - unresolved items or residual risk

## Versioning and Release Discipline
- Keep runtime/package version as a single source of truth in `src/raspi_sentinel/_version.py`.
- `CHANGELOG.md` policy:
  - released versions must have concrete dates
  - ongoing work goes under `[Unreleased]`
- When releasing:
  - align changelog + version string + tag (`vX.Y.Z`)
  - ensure release notes file exists under `docs/release-notes/`
