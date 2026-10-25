"""Small shared helpers."""

from __future__ import annotations

import os
from datetime import datetime, timezone


def safe_err(exc: BaseException) -> str:
    message = str(exc).replace("\n", " ")
    token = os.environ.get("X_BEARER_TOKEN", "")
    if token and token in message:
        message = message.replace(token, "[redacted]")
    return message[:300]


def iso(when: datetime) -> str:
    when = when.astimezone(timezone.utc).replace(microsecond=0)
    return when.strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(value: str) -> datetime:
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def relative(when: datetime, now: datetime) -> str:
    seconds = int((now - when).total_seconds())
    if seconds < 60:
        return "just now"
    if seconds < 3600:
        return f"{seconds // 60}m ago"
    if seconds < 86400:
        return f"{seconds // 3600}h ago"
    days = seconds // 86400
    if days == 1:
        return "yesterday"
    return f"{days}d ago"
