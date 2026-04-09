from __future__ import annotations

import json
import logging
import os
import platform
import shutil
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

from .checks import CheckResult
from .config import DiscordNotifyConfig

LOG = logging.getLogger(__name__)


@dataclass(slots=True)
class SystemSnapshot:
    uptime_sec: float
    load1: float
    load5: float
    load15: float
    disk_used_pct: float


def read_uptime_sec() -> float:
    try:
        with open("/proc/uptime", "r", encoding="utf-8") as fh:
            return float(fh.read().split()[0])
    except Exception:
        return 0.0


def collect_system_snapshot() -> SystemSnapshot:
    uptime = read_uptime_sec()
    try:
        load1, load5, load15 = os.getloadavg()
    except OSError:
        load1, load5, load15 = 0.0, 0.0, 0.0

    usage = shutil.disk_usage("/")
    if usage.total > 0:
        disk_used_pct = (usage.used / usage.total) * 100.0
    else:
        disk_used_pct = 0.0

    return SystemSnapshot(
        uptime_sec=uptime,
        load1=load1,
        load5=load5,
        load15=load15,
        disk_used_pct=disk_used_pct,
    )


class DiscordNotifier:
    def __init__(self, config: DiscordNotifyConfig) -> None:
        self.config = config

    @property
    def enabled(self) -> bool:
        return self.config.enabled and bool(self.config.webhook_url)

    def send_lines(self, title: str, lines: list[str], severity: str = "INFO") -> bool:
        if not self.enabled:
            return False

        host = platform.node() or "unknown-host"
        header = f"[{severity}] {title} ({host})"
        body_lines = [header] + [f"- {line}" for line in lines]
        content = "\n".join(body_lines)

        # Discord webhook message content limit is 2000 chars.
        if len(content) > 1900:
            content = content[:1897] + "..."

        payload = {
            "username": self.config.username,
            "content": content,
        }

        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            self.config.webhook_url or "",
            data=data,
            headers={
                "Content-Type": "application/json",
                "User-Agent": "raspi-sentinel/0.1 (+https://local.raspi-sentinel)",
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=self.config.timeout_sec) as resp:
                code = getattr(resp, "status", 204)
                if code not in (200, 204):
                    LOG.error("discord webhook returned status=%s", code)
                    return False
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            LOG.error("discord webhook HTTP error: %s %s", exc.code, detail[:300])
            return False
        except urllib.error.URLError as exc:
            LOG.error("discord webhook URL error: %s", exc)
            return False
        except TimeoutError:
            LOG.error("discord webhook timeout")
            return False
        except OSError as exc:
            LOG.error("discord webhook send failed: %s", exc)
            return False

        return True


def format_failures(result: CheckResult) -> str:
    if result.healthy:
        return "none"
    return "; ".join(f"{f.check}: {f.message}" for f in result.failures)


def should_send_periodic_heartbeat(
    state: dict[str, Any],
    interval_sec: int,
    now_ts: float,
) -> bool:
    notify_state = state.setdefault("notify", {})
    last_ts = notify_state.get("last_heartbeat_ts")
    if last_ts is None:
        return True
    try:
        elapsed = now_ts - float(last_ts)
    except (TypeError, ValueError):
        return True
    return elapsed >= interval_sec


def mark_heartbeat_sent(state: dict[str, Any], now_ts: float) -> None:
    notify_state = state.setdefault("notify", {})
    notify_state["last_heartbeat_ts"] = now_ts
