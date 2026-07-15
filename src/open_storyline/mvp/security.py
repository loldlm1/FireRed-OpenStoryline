from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable
import os
import re


SENSITIVE_KEY = re.compile(r"(?i)(authorization|api[_-]?key|password|secret|token)")
BEARER = re.compile(r"(?i)bearer\s+[a-z0-9._~+\-/=]+")
ASSIGNED_SECRET = re.compile(
    r"(?i)(api[_-]?key|password|secret|token)(\s*[:=]\s*)[\"']?[^\s,;\"']+"
)


def environment_secrets() -> list[str]:
    values = []
    for key, value in os.environ.items():
        if SENSITIVE_KEY.search(key) and len(value) >= 6:
            values.append(value)
    return sorted(set(values), key=len, reverse=True)


def sanitize_text(value: Any, secrets: Iterable[str] = (), limit: int = 4000) -> str:
    text = str(value or "")
    for secret in [*environment_secrets(), *secrets]:
        if secret and len(secret) >= 6:
            text = text.replace(secret, "***")
    text = BEARER.sub("Bearer ***", text)
    text = ASSIGNED_SECRET.sub(lambda match: f"{match.group(1)}{match.group(2)}***", text)
    return text[:limit]


def sanitize_for_persistence(value: Any, secrets: Iterable[str] = (), depth: int = 0) -> Any:
    if depth > 10:
        return "[truncated]"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return sanitize_text(value, secrets)
    if isinstance(value, Path):
        return sanitize_text(str(value), secrets)
    if isinstance(value, dict):
        clean: dict[str, Any] = {}
        for raw_key, item in list(value.items())[:200]:
            key = sanitize_text(raw_key, secrets, limit=200)
            clean[key] = "***" if SENSITIVE_KEY.search(key) else sanitize_for_persistence(
                item, secrets, depth + 1
            )
        return clean
    if isinstance(value, (list, tuple, set)):
        return [sanitize_for_persistence(item, secrets, depth + 1) for item in list(value)[:500]]
    return sanitize_text(value, secrets)
