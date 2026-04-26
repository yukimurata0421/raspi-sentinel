# Storage Tiers (Optional)

`raspi-sentinel` supports an optional storage split to reduce SD-card write amplification.

## 1. Tiers and responsibilities

- Volatile tier (tmpfs): high-frequency runtime writes
  - `snapshot_path` (`stats.json`)
  - `state_volatile_path` (runtime counters and transient state)
- Durable tier (disk): reboot-sensitive state
  - `state_durable_path` fields selected by `state_durable_fields`
  - typical durable fields:
    - `reboot_history`
    - `followup_schedule`
    - `notify_backlog`
- Event log tier (disk):
  - `events_path` (`events.jsonl`)

Example:

```toml
[storage]
snapshot_path = "/run/raspi-sentinel/stats.json"
state_volatile_path = "/run/raspi-sentinel/state.volatile.json"
state_durable_path = "/var/lib/raspi-sentinel/state.durable.json"
events_path = "/var/lib/raspi-sentinel/events.jsonl"
state_durable_fields = ["reboot_history", "followup_schedule", "notify_backlog"]
require_tmpfs = true
verify_min_free_bytes = 1048576
verify_write_bytes = 4096
verify_cooldown_sec = 2
```

`require_tmpfs` defaults to `false`. Enable it explicitly when you want strict tmpfs enforcement.
On low-memory models, tune tmpfs size with `RuntimeDirectorySize=` (or mount `size=` option)
to avoid pressure from volatile files.

`verify-storage` runs tmpfs-tier checks only when storage tiering is explicitly enabled by config.

Verification trigger conditions:

- `require_tmpfs = true`, or
- `state_durable_path` is configured, or
- `state_durable_fields` is non-empty.

State split conditions:

- `state_durable_path` must be configured for volatile/durable split to activate.
  (`is_storage_tiering_enabled(...)` is OR-based for verify triggers, but runtime split additionally requires durable store path.)
- `require_tmpfs = true` alone enables preflight verification only.

## 2. Write frequency by file

- `stats.json`: frequent (periodic + change-triggered) -> recommended tmpfs
- `state.volatile.json`: frequent (every cycle) -> recommended tmpfs
- `state.durable.json`: lower frequency but critical for reboot guards/backlog continuity -> disk
- `events.jsonl`: append-only transitions, lower frequency than snapshots -> disk

## 3. tmpfs mount and verification procedure

Recommended sequence before running `raspi-sentinel.service`:

1. Ensure `/run/raspi-sentinel` exists (created automatically when missing)
2. Attempt tmpfs mount (`run-raspi\x2dsentinel.mount` for `/run/raspi-sentinel`)
3. Verify mount status (`mount point`, `fs type`)
4. Verify owner/mode (`uid`, `gid`, `mode`)
5. Write/read probe file
6. Check free bytes (avoid write failure under size pressure)
7. Cooldown (`verify_cooldown_sec`)
8. Start `raspi-sentinel` process

Use:

```bash
raspi-sentinel -c /etc/raspi-sentinel/config.toml verify-storage --json
```

Cooldown intent:

- allow kernel/mount state to settle after mount activation
- ensure systemd dependency ordering has completed
- create a short time window for operator intervention before monitor action

## 4. Failure impact when durable tier is missing/reset

If durable files are lost or reset:

- reboot loop guard history can reset (`reboot_history`)
- pending follow-up schedule can be lost (`followup_schedule`)
- notification backlog/retry window can reset (`notify_backlog`)

Operationally, this means escalation memory can be shorter than intended until new durable state is rebuilt.

## 5. Non-goals

- Full "SD-free operation" is not a goal.
- `events.jsonl` and durable safety state are intentionally kept on persistent storage by default.
- This feature does not replace filesystem/system-level durability controls (power-loss handling, fs checks, watchdog strategy).
