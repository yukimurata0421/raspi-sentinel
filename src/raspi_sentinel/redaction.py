from __future__ import annotations

import re

_URL_CREDENTIALS_RE = re.compile(r"(https?://)([^/\s:@]+):([^@\s/]+)@")
_QUERY_SECRET_RE = re.compile(
    r"([?&](?:token|apikey|api_key|secret|password|passwd|pwd|key)=)([^&\s]+)",
    re.IGNORECASE,
)
_AUTH_HEADER_RE = re.compile(
    r"((?:authorization|x-api-key|api-key)\s*[:=]\s*)([^\s\"']+)",
    re.IGNORECASE,
)
_BEARER_RE = re.compile(r"(bearer\s+)([A-Za-z0-9\-._~+/=]+)", re.IGNORECASE)
_HOME_PATH_RE = re.compile(r"/home/[^/\s]+")
_DISCORD_WEBHOOK_RE = re.compile(
    r"https://(?:discord(?:app)?\.com)/api/webhooks/[^/\s]+/[^/\s]+",
    re.IGNORECASE,
)


def redact_text(text: str) -> str:
    redacted = _BEARER_RE.sub(r"\1***", text)
    redacted = _URL_CREDENTIALS_RE.sub(r"\1***:***@", redacted)
    redacted = _QUERY_SECRET_RE.sub(r"\1***", redacted)
    redacted = _AUTH_HEADER_RE.sub(r"\1***", redacted)
    redacted = _HOME_PATH_RE.sub("/home/<redacted>", redacted)
    redacted = _DISCORD_WEBHOOK_RE.sub("https://discord.com/api/webhooks/<redacted>", redacted)
    return redacted


def redact_command(command: str) -> str:
    return redact_text(command)
