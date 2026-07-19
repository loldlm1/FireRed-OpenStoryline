from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
import asyncio
import json
import re

from open_storyline.mvp.observability import emit_event


if TYPE_CHECKING:
    from open_storyline.mvp.jobs import JobStore


ACTIVITY_SCHEMA_VERSION = 1
TERMINAL_STATES = frozenset({"completed", "failed", "cancelled"})
ACTIVITY_CATEGORIES = frozenset(
    {"queue", "analysis", "provider", "planning", "asset", "render", "qa", "system"}
)
ACTIVITY_STATUSES = frozenset(
    {"queued", "started", "progress", "completed", "skipped", "warning", "failed"}
)
MESSAGE_KEY_PATTERN = re.compile(r"^activity\.[a-z0-9_.]{1,110}$")
SAFE_LABEL_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._:/+-]{0,79}$")
SAFE_CODE_PATTERN = re.compile(r"^[A-Z0-9][A-Z0-9_:-]{0,119}$")
PUBLIC_NUMERIC_FIELDS = frozenset(
    {
        "attempt_number",
        "selected_clips",
        "sampled_frames",
        "asset_count",
        "clip_count",
    }
)
PUBLIC_FIELDS = frozenset(
    {
        "category",
        "status",
        "message_key",
        "progress",
        "current",
        "total",
        "elapsed_ms",
        "retryable",
        "provider",
        "tool",
        "error_code",
        *PUBLIC_NUMERIC_FIELDS,
    }
)
RETRYABLE_ERROR_CODES = frozenset(
    {
        "DATABASE_UNAVAILABLE",
        "JOB_STATE_UNAVAILABLE",
        "NINEROUTER_REQUEST_FAILED",
        "NINEROUTER_RATE_LIMITED",
        "MISTRAL_STT_REQUEST_FAILED",
        "MISTRAL_STT_RATE_LIMITED",
        "PEXELS_SEARCH_FAILED",
        "PEXELS_DOWNLOAD_FAILED",
        "REMOTE_IMAGE_REQUEST_FAILED",
    }
)


def _error(code: str, message: str) -> RuntimeError:
    from open_storyline.mvp.jobs import JobStoreError

    return JobStoreError(code, message)


@dataclass(frozen=True)
class ActivityStage:
    progress: float
    category: str
    message_key: str
    provider: str | None = None
    tool: str | None = None


STAGES: dict[str, ActivityStage] = {
    "queued": ActivityStage(0.05, "queue", "activity.queue.waiting"),
    "starting": ActivityStage(0.10, "system", "activity.system.starting"),
    "extracting_audio": ActivityStage(
        0.18,
        "analysis",
        "activity.analysis.extracting_audio",
        tool="FFmpeg",
    ),
    "remote_transcription": ActivityStage(
        0.28,
        "provider",
        "activity.provider.transcribing",
        provider="Mistral",
        tool="Voxtral",
    ),
    "detecting_scenes": ActivityStage(
        0.42,
        "analysis",
        "activity.analysis.detecting_scenes",
        tool="FFmpeg",
    ),
    "sampling_frames": ActivityStage(
        0.48,
        "analysis",
        "activity.analysis.sampling_frames",
        tool="FFmpeg",
    ),
    "sampling_agentic_frames": ActivityStage(
        0.48,
        "analysis",
        "activity.analysis.sampling_frames",
        tool="FFmpeg",
    ),
    "remote_visual_understanding": ActivityStage(
        0.54,
        "provider",
        "activity.provider.understanding_video",
        provider="9Router",
        tool="Visual understanding",
    ),
    "remote_planning": ActivityStage(
        0.58,
        "planning",
        "activity.planning.selecting_clips",
        provider="9Router",
        tool="Clip planner",
    ),
    "planning_agentic_edit": ActivityStage(
        0.62,
        "planning",
        "activity.planning.designing_edit",
        provider="9Router",
        tool="Edit planner",
    ),
    "resolving_assets": ActivityStage(
        0.66,
        "asset",
        "activity.asset.resolving",
        tool="Asset resolver",
    ),
    "rendering": ActivityStage(
        0.68,
        "render",
        "activity.render.starting",
        tool="FFmpeg",
    ),
    "planning_effects": ActivityStage(
        0.88,
        "planning",
        "activity.planning.effects",
        provider="9Router",
        tool="Effects planner",
    ),
    "post_render_qa": ActivityStage(
        0.94,
        "qa",
        "activity.qa.checking_outputs",
        tool="Deterministic QA",
    ),
    "packaging": ActivityStage(
        0.98,
        "system",
        "activity.system.packaging",
        tool="Artifact registry",
    ),
}


def retryable_error(code: str | None) -> bool:
    normalized = str(code or "").strip().upper()
    return normalized in RETRYABLE_ERROR_CODES or normalized.endswith(
        ("_TIMEOUT", "_RATE_LIMITED", "_UNAVAILABLE")
    )


def normalize_activity(payload: Mapping[str, Any]) -> dict[str, Any]:
    unknown = set(payload) - PUBLIC_FIELDS
    if unknown:
        raise _error("ACTIVITY_FIELD_INVALID", "public activity contains invalid fields")

    category = str(payload.get("category") or "")
    status = str(payload.get("status") or "")
    message_key = str(payload.get("message_key") or "")
    if category not in ACTIVITY_CATEGORIES:
        raise _error("ACTIVITY_CATEGORY_INVALID", "public activity category is invalid")
    if status not in ACTIVITY_STATUSES:
        raise _error("ACTIVITY_STATUS_INVALID", "public activity status is invalid")
    if not MESSAGE_KEY_PATTERN.fullmatch(message_key):
        raise _error("ACTIVITY_MESSAGE_INVALID", "public activity message is invalid")

    clean: dict[str, Any] = {
        "schema_version": ACTIVITY_SCHEMA_VERSION,
        "category": category,
        "status": status,
        "message_key": message_key,
    }
    if payload.get("progress") is not None:
        progress = float(payload["progress"])
        if not 0 <= progress <= 1:
            raise _error("ACTIVITY_PROGRESS_INVALID", "public activity progress is invalid")
        clean["progress"] = round(progress, 4)

    for field in ("current", "total", "elapsed_ms", *sorted(PUBLIC_NUMERIC_FIELDS)):
        if payload.get(field) is None:
            continue
        value = int(payload[field])
        maximum = 604_800_000 if field == "elapsed_ms" else 100_000
        if value < 0 or value > maximum:
            raise _error("ACTIVITY_VALUE_INVALID", "public activity value is invalid")
        clean[field] = value
    if ("current" in clean or "total" in clean) and (
        "current" not in clean
        or "total" not in clean
        or clean["total"] < 1
        or clean["current"] > clean["total"]
    ):
        raise _error("ACTIVITY_COUNT_INVALID", "public activity count is invalid")

    if payload.get("retryable") is not None:
        if not isinstance(payload["retryable"], bool):
            raise _error("ACTIVITY_RETRY_INVALID", "public activity retry flag is invalid")
        clean["retryable"] = payload["retryable"]
    for field in ("provider", "tool"):
        if payload.get(field) is None:
            continue
        value = str(payload[field]).strip()
        if not SAFE_LABEL_PATTERN.fullmatch(value):
            raise _error("ACTIVITY_LABEL_INVALID", "public activity label is invalid")
        clean[field] = value
    if payload.get("error_code") is not None:
        error_code = str(payload["error_code"]).strip().upper()
        if not SAFE_CODE_PATTERN.fullmatch(error_code):
            raise _error("ACTIVITY_ERROR_INVALID", "public activity error is invalid")
        clean["error_code"] = error_code
    if status == "failed" and (
        "error_code" not in clean or "retryable" not in clean
    ):
        raise _error("ACTIVITY_FAILURE_INVALID", "failed activity requires safe failure metadata")
    return clean


def encode_sse(*, sequence: int, event: Mapping[str, Any]) -> bytes:
    compact = json.dumps(event, ensure_ascii=True, separators=(",", ":"))
    return f"id: {int(sequence)}\nevent: activity\ndata: {compact}\n\n".encode("utf-8")


class ActivityService:
    def __init__(
        self,
        store: JobStore,
        *,
        poll_interval: float = 0.5,
        heartbeat_interval: float = 15.0,
    ) -> None:
        self.store = store
        self.poll_interval = max(0.05, float(poll_interval))
        self.heartbeat_interval = max(self.poll_interval, float(heartbeat_interval))

    async def emit(
        self,
        job_id: str,
        *,
        stage: str | None = None,
        **payload: Any,
    ) -> dict[str, Any]:
        clean = normalize_activity(payload)
        return await self.store.record_public_event(job_id, stage=stage, payload=clean)

    async def emit_safely(
        self,
        job_id: str,
        *,
        stage: str | None = None,
        **payload: Any,
    ) -> dict[str, Any] | None:
        try:
            return await self.emit(job_id, stage=stage, **payload)
        except Exception as exc:
            code = str(getattr(exc, "code", "ACTIVITY_STORAGE_UNAVAILABLE"))
            if code == "ACTIVITY_PROGRESS_INVALID" and payload.get("progress") is not None:
                try:
                    state = await self.store.load(job_id)
                    retry_payload = dict(payload)
                    retry_payload["progress"] = max(
                        float(payload["progress"]),
                        float(state.get("progress") or 0),
                    )
                    return await self.emit(job_id, stage=stage, **retry_payload)
                except Exception:
                    pass
            emit_event(
                "public_activity_record_failed",
                job_id=job_id,
                stage=stage,
                error_code=code,
            )
            return None

    async def stage(
        self,
        job_id: str,
        stage: str,
        *,
        status: str = "started",
        **metadata: Any,
    ) -> dict[str, Any]:
        definition = STAGES[stage]
        state = await self.store.load(job_id)
        progress = max(definition.progress, float(state.get("progress") or 0))
        await self.store.update(job_id, progress=progress, stage=stage)
        result = await self.emit_safely(
            job_id,
            stage=stage,
            category=definition.category,
            status=status,
            message_key=definition.message_key,
            progress=progress,
            provider=definition.provider,
            tool=definition.tool,
            **metadata,
        )
        return result or {}

    async def list(
        self,
        job_id: str,
        *,
        after_sequence: int = 0,
        limit: int = 100,
    ) -> dict[str, Any]:
        if not 0 <= int(after_sequence) <= 2_147_483_647:
            raise _error("EVENT_CURSOR_INVALID", "event cursor is invalid")
        if not 1 <= int(limit) <= 200:
            raise _error("PAGE_LIMIT_INVALID", "event limit is invalid")
        events, state = await self.store.public_events(
            job_id,
            after_sequence=int(after_sequence),
            limit=int(limit),
        )
        return {
            "items": events,
            "next_after": events[-1]["sequence"] if events else int(after_sequence),
            "terminal": state in TERMINAL_STATES,
            "state": state,
        }

    async def stream(
        self,
        job_id: str,
        *,
        after_sequence: int = 0,
        disconnected: Callable[[], Awaitable[bool]] | None = None,
    ) -> AsyncIterator[bytes]:
        cursor = int(after_sequence)
        loop = asyncio.get_running_loop()
        last_output = loop.time()
        while True:
            if disconnected is not None and await disconnected():
                return
            page = await self.list(job_id, after_sequence=cursor, limit=100)
            for event in page["items"]:
                cursor = int(event["sequence"])
                last_output = loop.time()
                yield encode_sse(sequence=cursor, event=event)
            if page["terminal"] and not page["items"]:
                return
            if loop.time() - last_output >= self.heartbeat_interval:
                last_output = loop.time()
                yield b": heartbeat\n\n"
            await asyncio.sleep(self.poll_interval)
