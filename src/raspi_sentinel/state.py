from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from .state_helpers import write_json_atomic

LOG = logging.getLogger(__name__)


DEFAULT_STATE: dict[str, Any] = {
    "targets": {},
    "reboots": [],
    "followups": {},
    "notify": {},
    "monitor_stats": {},
}


class StateStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"targets": {}, "reboots": [], "followups": {}, "notify": {}, "monitor_stats": {}}

        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            LOG.error("state file is invalid JSON (%s): %s", self.path, exc)
            return {"targets": {}, "reboots": [], "followups": {}, "notify": {}, "monitor_stats": {}}
        except OSError as exc:
            LOG.error("cannot read state file %s: %s", self.path, exc)
            return {"targets": {}, "reboots": [], "followups": {}, "notify": {}, "monitor_stats": {}}

        if not isinstance(data, dict):
            LOG.error("state file root must be object: %s", self.path)
            return {"targets": {}, "reboots": [], "followups": {}, "notify": {}, "monitor_stats": {}}

        targets = data.get("targets")
        reboots = data.get("reboots")
        followups = data.get("followups")
        notify = data.get("notify")
        monitor_stats = data.get("monitor_stats")
        if not isinstance(targets, dict):
            targets = {}
        if not isinstance(reboots, list):
            reboots = []
        if not isinstance(followups, dict):
            followups = {}
        if not isinstance(notify, dict):
            notify = {}
        if not isinstance(monitor_stats, dict):
            monitor_stats = {}

        return {
            "targets": targets,
            "reboots": reboots,
            "followups": followups,
            "notify": notify,
            "monitor_stats": monitor_stats,
        }

    def save(self, state: dict[str, Any]) -> None:
        write_json_atomic(self.path, state, indent=2)
