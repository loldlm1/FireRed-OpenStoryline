from __future__ import annotations

from contextvars import ContextVar, Token
from datetime import UTC, datetime
from typing import Any
import json
import logging
import re
import uuid

from open_storyline.mvp.security import sanitize_text


LOGGER = logging.getLogger("openstoryline.mvp")
REQUEST_ID = ContextVar("openstoryline_mvp_request_id", default="")
SAFE_ID = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")


def start_request(request_id: str | None = None) -> tuple[str, Token[str]]:
    candidate = str(request_id or "").strip()
    identifier = candidate if SAFE_ID.fullmatch(candidate) else uuid.uuid4().hex
    return identifier, REQUEST_ID.set(identifier)


def finish_request(token: Token[str]) -> None:
    REQUEST_ID.reset(token)


def emit_event(
    event_name: str,
    *,
    editing_session_id: str | None = None,
    job_id: str | None = None,
    stage: str | None = None,
    duration_ms: int | None = None,
    outcome: str | None = None,
    error_code: str | None = None,
    **fields: Any,
) -> None:
    payload: dict[str, Any] = {
        "timestamp": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "event": sanitize_text(event_name, limit=80),
        "request_id": REQUEST_ID.get() or None,
        "editing_session_id": editing_session_id,
        "job_id": job_id,
        "stage": sanitize_text(stage, limit=64) if stage else None,
        "duration_ms": max(0, int(duration_ms)) if duration_ms is not None else None,
        "outcome": sanitize_text(outcome, limit=40) if outcome else None,
        "error_code": sanitize_text(error_code, limit=120) if error_code else None,
    }
    for key, value in fields.items():
        if value is None or isinstance(value, (bool, int, float)):
            payload[sanitize_text(key, limit=80)] = value
        elif isinstance(value, str):
            payload[sanitize_text(key, limit=80)] = sanitize_text(value, limit=200)
    try:
        LOGGER.info(json.dumps(payload, separators=(",", ":"), sort_keys=True))
    except Exception:
        # Logging is diagnostic only; durable events remain in PostgreSQL.
        return
