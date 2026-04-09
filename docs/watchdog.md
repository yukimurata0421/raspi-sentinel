# Watchdog Integration (Optional)

`raspi-sentinel` core is responsible for:

- logical health monitoring while Linux is alive
- staged recovery (warn -> restart -> reboot escalation)

Hardware/system watchdog is a lower-level failsafe and optional integration.

## Why Separate It

- keeps core logic testable and reusable
- avoids coupling core behavior to watchdog protocol details
- allows projects to choose distro/hardware-specific watchdog setup

## Integration Options

## 1. Hardware watchdog through systemd PID1

Set in `/etc/systemd/system.conf`:

```ini
RuntimeWatchdogSec=20s
RebootWatchdogSec=30s
```

And enable Pi watchdog device (distro dependent, for example):

```ini
dtparam=watchdog=on
```

## 2. Optional service-level watchdog bridge

Example unit:

- `examples/watchdog/raspi-sentinel-watchdog-integration.service`

This bridge runs `raspi-sentinel run-once` in a loop and emits `WATCHDOG=1` to systemd.
If the bridge process hangs, systemd can restart it by `WatchdogSec`.

## Operational Note

Even with hardware watchdog enabled, service-level restart/reboot policy remains in `raspi-sentinel` config.
Watchdog is only a lower-level fallback for deeper hangs.
