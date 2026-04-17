# Exit Codes

`raspi-sentinel` CLI uses stable numeric exit codes for automation and systemd integration.

| Code | Name | Meaning |
| --- | --- | --- |
| `0` | `OK` | Cycle completed and all targets are `ok`. |
| `1` | `UNHEALTHY` | Cycle completed but at least one target is `degraded`/`failed` (or limited-mode degraded). |
| `2` | `REBOOT_REQUESTED` | Recovery logic decided reboot is required and requested. |
| `10` | `CONFIG_LOAD_FAILED` | Config file could not be parsed/validated. |
| `11` | `INVALID_INTERVAL` | `loop --interval-sec` value is invalid (`<= 0`). |
| `13` | `STATE_LOCK_ERROR` | Could not acquire state lock or lock I/O failed. |
| `14` | `STATE_PERSIST_FAILED` | Cycle ran but `state.json` persistence failed. |
| `15` | `VALIDATION_WARNING` | `validate-config --strict` detected warnings. |

Implementation source: [src/raspi_sentinel/exit_codes.py](../../src/raspi_sentinel/exit_codes.py).
