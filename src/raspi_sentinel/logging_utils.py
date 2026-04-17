from __future__ import annotations

import json
import logging
from datetime import datetime, timezone


class _JsonLogFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def configure_logging(verbose: bool = False, structured: bool = False) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    if structured:
        handler = logging.StreamHandler()
        handler.setFormatter(_JsonLogFormatter())
        root = logging.getLogger()
        root.handlers.clear()
        root.setLevel(level)
        root.addHandler(handler)
        return
    logging.basicConfig(level=level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
