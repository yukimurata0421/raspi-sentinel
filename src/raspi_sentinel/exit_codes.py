from __future__ import annotations

# Process completed successfully.
OK = 0
# At least one target is unhealthy/degraded (non-fatal for process runtime).
UNHEALTHY = 1
# Recovery decided that host reboot is required.
REBOOT_REQUESTED = 2

# CLI/input/runtime setup errors.
CONFIG_LOAD_FAILED = 10
INVALID_INTERVAL = 11
STATE_LOCK_ERROR = 13
STATE_PERSIST_FAILED = 14
VALIDATION_WARNING = 15
