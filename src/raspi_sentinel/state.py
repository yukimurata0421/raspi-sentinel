from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

LOG = logging.getLogger(__name__)


DEFAULT_STATE: dict[str, Any] = {
    "targets": {},
    "reboots": [],
    "followups": {},
    "notify": {},
}


class StateStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"targets": {}, "reboots": [], "followups": {}, "notify": {}}

        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            LOG.error("state file is invalid JSON (%s): %s", self.path, exc)
            return {"targets": {}, "reboots": [], "followups": {}, "notify": {}}
        except OSError as exc:
            LOG.error("cannot read state file %s: %s", self.path, exc)
            return {"targets": {}, "reboots": [], "followups": {}, "notify": {}}

        if not isinstance(data, dict):
            LOG.error("state file root must be object: %s", self.path)
            return {"targets": {}, "reboots": [], "followups": {}, "notify": {}}

        targets = data.get("targets")
        reboots = data.get("reboots")
        followups = data.get("followups")
        notify = data.get("notify")
        if not isinstance(targets, dict):
            targets = {}
        if not isinstance(reboots, list):
            reboots = []
        if not isinstance(followups, dict):
            followups = {}
        if not isinstance(notify, dict):
            notify = {}

        return {
            "targets": targets,
            "reboots": reboots,
            "followups": followups,
            "notify": notify,
        }

    def save(self, state: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self.path.with_suffix(self.path.suffix + ".tmp")

        text = json.dumps(state, sort_keys=True, indent=2)
        tmp_path.write_text(text + "\n", encoding="utf-8")
        tmp_path.replace(self.path)
