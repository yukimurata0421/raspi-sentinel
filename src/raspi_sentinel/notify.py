from __future__ import annotations

import json
import logging
import os
import platform
import shutil
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

from ._version import __version__
from .checks import CheckResult
from .config import DiscordNotifyConfig
from .state_helpers import read_uptime_sec
from .state_models import GlobalState

LOG = logging.getLogger(__name__)


@dataclass(slots=True)
class SystemSnapshot:
    uptime_sec: float
    load1: float
    load5: float
    load15: float
    disk_used_pct: float


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
        self.last_failure_kind: str | None = None

    @property
    def enabled(self) -> bool:
        return self.config.enabled and bool(self.config.webhook_url)

    def send_lines(self, title: str, lines: list[str], severity: str = "INFO") -> bool:
        if not self.enabled:
            self.last_failure_kind = None
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
        max_attempts = 3
        self.last_failure_kind = None
        for attempt in range(max_attempts):
            ok, retry_after_sec, failure_kind = self._post_discord_payload(data)
            if ok:
                self.last_failure_kind = None
                return True
            self.last_failure_kind = failure_kind
            if attempt < max_attempts - 1:
                backoff_sec = self.config.retry_backoff_base_sec * (attempt + 1)
                sleep_sec = retry_after_sec if retry_after_sec is not None else backoff_sec
                time.sleep(max(0.0, sleep_sec))
                LOG.warning(
                    "discord webhook send retry attempt %s/%s",
                    attempt + 2,
                    max_attempts,
                )
        return False

    def _post_discord_payload(self, data: bytes) -> tuple[bool, float | None, str | None]:
        req = urllib.request.Request(
            self.config.webhook_url or "",
            data=data,
            headers={
                "Content-Type": "application/json",
                "User-Agent": f"raspi-sentinel/{__version__}",
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=self.config.timeout_sec) as resp:
                code = getattr(resp, "status", 204)
                if code not in (200, 204):
                    LOG.error("discord webhook returned status=%s", code)
                    if code >= 500:
                        return False, None, "network"
                    return False, None, "http"
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            LOG.error("discord webhook HTTP error: %s %s", exc.code, detail[:300])
            retry_after = _parse_retry_after_seconds(exc.headers.get("Retry-After"))
            if exc.code == 429 or exc.code >= 500:
                return False, retry_after, "network"
            return False, retry_after, "http"
        except urllib.error.URLError as exc:
            LOG.error("discord webhook URL error: %s", exc)
            return False, None, "network"
        except TimeoutError:
            LOG.error("discord webhook timeout")
            return False, None, "network"
        except OSError as exc:
            LOG.error("discord webhook send failed: %s", exc)
            return False, None, "network"

        return True, None, None


def _parse_retry_after_seconds(raw_value: Any) -> float | None:
    if raw_value is None:
        return None
    try:
        value = float(raw_value)
    except (TypeError, ValueError):
        return None
    return max(0.0, value)


def format_failures(result: CheckResult) -> str:
    if result.healthy:
        return "none"
    pr = result.observations.get("policy_reason")
    if isinstance(pr, str) and pr.strip():
        return pr
    if result.failures:
        return "; ".join(f"{f.check}: {f.message}" for f in result.failures)
    return "unhealthy"


def should_send_periodic_heartbeat(
    state: GlobalState,
    interval_sec: int,
    now_ts: float,
) -> bool:
    last_ts = state.notify.last_heartbeat_ts
    if last_ts is None:
        return True
    try:
        elapsed = now_ts - float(last_ts)
    except (TypeError, ValueError):
        return True
    return elapsed >= interval_sec


def mark_heartbeat_sent(state: GlobalState, now_ts: float) -> None:
    state.notify.last_heartbeat_ts = now_ts
