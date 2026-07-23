from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional, Protocol
import asyncio
import base64
import binascii
import hashlib
import json
import os
import re
import shutil
import uuid
import zipfile

from sqlalchemy import and_, func, or_, select, text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession

from open_storyline.mvp.activity import ActivityService, retryable_error
from open_storyline.mvp.database import Database
from open_storyline.mvp.edit_plan import (
    EditPlanError,
    validate_generated_asset_limit,
    validate_asset_policy,
    validate_stock_asset_limit,
    validate_stock_asset_kind,
    validate_stock_policy,
)
from open_storyline.mvp.models import (
    Artifact,
    EditingSession,
    JobEvent,
    SessionInputVideo,
    VideoJob,
)
from open_storyline.mvp.observability import emit_event
from open_storyline.mvp.outcomes import build_failed_outcome_report
from open_storyline.mvp.repair import RepairContractError, validate_repair_report
from open_storyline.mvp.security import sanitize_for_persistence, sanitize_text


JOB_ID_PATTERN = re.compile(r"^[a-f0-9]{32}$")
TERMINAL_STATES = {"completed", "failed", "cancelled"}
ACTIVE_STATES = {"uploading", "queued", "running"}
WORKER_ADVISORY_LOCK = 7_303_110_792_761
CAPACITY_ADVISORY_LOCK = 7_303_110_792_762
WORKER_EXECUTION_ADVISORY_LOCK = 7_303_110_792_763
MEDIA_ARTIFACT_SUFFIXES = {
    ".avi",
    ".jpeg",
    ".jpg",
    ".m4v",
    ".mkv",
    ".mov",
    ".mp4",
    ".png",
    ".webm",
    ".webp",
    ".zip",
}
Processor = Callable[[str, "JobStore"], Awaitable[dict[str, Any] | None]]


class AuditObserver(Protocol):
    async def ingest_artifact(self, job_id: str, artifact_name: str) -> dict[str, Any]: ...

    async def ingest_job_snapshot(self, job_id: str) -> dict[str, Any]: ...

    async def verify_job(self, job_id: str) -> dict[str, Any]: ...


class RetentionObserver(Protocol):
    async def cleanup_terminal_job(self, job_id: str) -> dict[str, Any]: ...


class JobStoreError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"{code}: {message}")


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _safe_filename(value: str) -> str:
    name = Path(str(value or "video.mp4").replace("\\", "/")).name
    stem = re.sub(r"[^a-zA-Z0-9._-]+", "-", name).strip(".-")
    return (stem or "video.mp4")[:180]


def _bounded_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)).strip())
    except ValueError:
        raise JobStoreError("JOB_CONFIG_INVALID", f"{name} must be an integer") from None
    if not minimum <= value <= maximum:
        raise JobStoreError(
            "JOB_CONFIG_INVALID",
            f"{name} must be between {minimum} and {maximum}",
        )
    return value


def _encode_cursor(timestamp: datetime, item_id: str) -> str:
    payload = json.dumps([_iso(timestamp), item_id], separators=(",", ":"))
    return base64.urlsafe_b64encode(payload.encode("utf-8")).decode("ascii").rstrip("=")


def _decode_cursor(cursor: str | None) -> tuple[datetime, str] | None:
    if not cursor:
        return None
    if len(cursor) > 512:
        raise JobStoreError("CURSOR_INVALID", "pagination cursor is invalid")
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        timestamp, item_id = json.loads(
            base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8")
        )
        parsed = datetime.fromisoformat(str(timestamp).replace("Z", "+00:00"))
    except (ValueError, TypeError, json.JSONDecodeError, UnicodeError, binascii.Error):
        raise JobStoreError("CURSOR_INVALID", "pagination cursor is invalid") from None
    if parsed.tzinfo is None or not JOB_ID_PATTERN.fullmatch(str(item_id)):
        raise JobStoreError("CURSOR_INVALID", "pagination cursor is invalid")
    return parsed.astimezone(UTC), str(item_id)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _parse_legacy_time(value: Any, fallback: datetime) -> datetime:
    if not value:
        return fallback
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return fallback
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


class JobStore:
    """PostgreSQL state with validated filesystem paths and rollback snapshots."""

    def __init__(
        self,
        root: str | Path,
        database: Database,
        *,
        max_active_jobs: int | None = None,
        media_retention_days: int | None = None,
        audit_retention_days: int | None = None,
        session_media_root: str | Path | None = None,
    ) -> None:
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.database = database
        self.session_media_root = (
            Path(session_media_root).expanduser().resolve()
            if session_media_root is not None
            else (self.root.parent / "mvp_sessions").resolve()
        )
        self.max_active_jobs = max_active_jobs or _bounded_int(
            "OPENSTORYLINE_MAX_ACTIVE_JOBS", 20, 1, 1000
        )
        self.media_retention = timedelta(
            days=media_retention_days
            if media_retention_days is not None
            else _bounded_int("OPENSTORYLINE_MEDIA_RETENTION_DAYS", 7, 1, 365)
        )
        self.audit_retention = timedelta(
            days=audit_retention_days
            if audit_retention_days is not None
            else _bounded_int("OPENSTORYLINE_AUDIT_RETENTION_DAYS", 30, 1, 3650)
        )
        self.audit: AuditObserver | None = None
        self.retention: RetentionObserver | None = None

    def attach_audit(self, observer: AuditObserver) -> None:
        self.audit = observer

    def attach_retention(self, observer: RetentionObserver) -> None:
        self.retention = observer

    @staticmethod
    def _artifact_expiry(
        job: VideoJob,
        *,
        kind: str,
        relative_path: str,
    ) -> datetime | None:
        if kind == "video" or Path(relative_path).suffix.lower() in MEDIA_ARTIFACT_SUFFIXES:
            return job.media_expires_at
        return job.audit_expires_at

    async def _audit_safely(
        self,
        job_id: str,
        operation: str,
        action: Callable[[], Awaitable[Any]],
    ) -> None:
        try:
            await action()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            code = sanitize_text(
                getattr(exc, "code", "AUDIT_OPERATION_FAILED"),
                limit=120,
            )
            emit_event(
                "audit_operation_failed",
                job_id=job_id,
                outcome="error",
                error_code=code,
                operation=operation,
            )
            try:
                await self.record_event(
                    job_id,
                    "audit_operation_failed",
                    {"operation": operation, "code": code},
                )
            except Exception:
                pass

    async def _retention_safely(self, job_id: str) -> None:
        if self.retention is None:
            return
        try:
            await self.retention.cleanup_terminal_job(job_id)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            code = sanitize_text(
                getattr(exc, "code", "RETENTION_OPERATION_FAILED"),
                limit=120,
            )
            emit_event(
                "retention_operation_failed",
                job_id=job_id,
                outcome="error",
                error_code=code,
            )
            try:
                await self.record_event(
                    job_id,
                    "retention_operation_failed",
                    {"operation": "cleanup_terminal_job", "code": code},
                )
            except Exception:
                pass

    def _job_dir(self, job_id: str) -> Path:
        if not JOB_ID_PATTERN.fullmatch(str(job_id or "")):
            raise JobStoreError("JOB_ID_INVALID", "invalid job id")
        return self.root / job_id

    def _state_path(self, job_id: str) -> Path:
        return self._job_dir(job_id) / "job.json"

    @staticmethod
    def _write_atomic(path: Path, value: dict[str, Any]) -> None:
        temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        payload = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)
        try:
            with temporary.open("x", encoding="utf-8") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)

    def _prepare_job_directories(
        self,
        job_id: str,
        *,
        include_input: bool = True,
    ) -> Path:
        job_dir = self._job_dir(job_id)
        job_dir.mkdir(parents=True)
        if include_input:
            (job_dir / "input").mkdir()
        (job_dir / "output").mkdir()
        (job_dir / "work").mkdir()
        return job_dir

    async def create_session(
        self,
        title: str,
        *,
        session_id: str | None = None,
        workflow_version: int = 2,
    ) -> dict[str, Any]:
        clean_title = str(title or "").strip()
        if not clean_title:
            raise JobStoreError("SESSION_TITLE_REQUIRED", "a session title is required")
        if len(clean_title) > 160:
            raise JobStoreError("SESSION_TITLE_INVALID", "session title is too long")
        identifier = session_id or uuid.uuid4().hex
        if not JOB_ID_PATTERN.fullmatch(identifier):
            raise JobStoreError("SESSION_ID_INVALID", "invalid session id")
        if int(workflow_version) not in {1, 2}:
            raise JobStoreError(
                "SESSION_WORKFLOW_VERSION_INVALID", "invalid session workflow version"
            )
        now = _utcnow()
        row = EditingSession(
            id=identifier,
            title=clean_title,
            workflow_version=int(workflow_version),
            updated_at=now,
            audit_expires_at=now + self.audit_retention,
        )
        try:
            async with self.database.sessions() as session:
                async with session.begin():
                    session.add(row)
        except IntegrityError:
            raise JobStoreError("SESSION_ALREADY_EXISTS", "session already exists") from None
        except SQLAlchemyError:
            raise JobStoreError("DATABASE_UNAVAILABLE", "session storage is unavailable") from None
        return self._session_state(row, None)

    async def get_session(self, session_id: str) -> dict[str, Any]:
        if not JOB_ID_PATTERN.fullmatch(str(session_id or "")):
            raise JobStoreError("SESSION_ID_INVALID", "invalid session id")
        try:
            async with self.database.sessions() as session:
                row = await session.get(EditingSession, session_id)
                input_video = await session.scalar(
                    select(SessionInputVideo).where(
                        SessionInputVideo.editing_session_id == session_id
                    )
                )
        except SQLAlchemyError:
            raise JobStoreError("DATABASE_UNAVAILABLE", "session storage is unavailable") from None
        if (
            row is None
            or row.deleted_at is not None
            or row.audit_expires_at <= _utcnow()
        ):
            raise JobStoreError("SESSION_NOT_FOUND", "session not found")
        return self._session_state(row, input_video)

    async def list_sessions(
        self,
        *,
        limit: int = 20,
        cursor: str | None = None,
        workflow_version: int | None = None,
    ) -> dict[str, Any]:
        if not 1 <= int(limit) <= 50:
            raise JobStoreError("PAGE_LIMIT_INVALID", "limit must be between 1 and 50")
        boundary = _decode_cursor(cursor)
        now = _utcnow()
        query = (
            select(EditingSession)
            .where(
                EditingSession.deleted_at.is_(None),
                EditingSession.audit_expires_at > now,
            )
            .order_by(EditingSession.updated_at.desc(), EditingSession.id.desc())
            .limit(int(limit) + 1)
        )
        if workflow_version is not None:
            if int(workflow_version) not in {1, 2}:
                raise JobStoreError(
                    "SESSION_WORKFLOW_VERSION_INVALID",
                    "invalid session workflow version",
                )
            query = query.where(
                EditingSession.workflow_version == int(workflow_version)
            )
        if boundary:
            timestamp, item_id = boundary
            query = query.where(
                or_(
                    EditingSession.updated_at < timestamp,
                    and_(
                        EditingSession.updated_at == timestamp,
                        EditingSession.id < item_id,
                    ),
                )
            )
        try:
            async with self.database.sessions() as session:
                rows = list((await session.execute(query)).scalars())
                selected = rows[: int(limit)]
                input_videos = (
                    list(
                        (
                            await session.execute(
                                select(SessionInputVideo).where(
                                    SessionInputVideo.editing_session_id.in_(
                                        [row.id for row in selected]
                                    )
                                )
                            )
                        ).scalars()
                    )
                    if selected
                    else []
                )
        except SQLAlchemyError:
            raise JobStoreError("DATABASE_UNAVAILABLE", "session storage is unavailable") from None
        has_more = len(rows) > int(limit)
        items = selected
        input_videos_by_session = {
            item.editing_session_id: item for item in input_videos
        }
        return {
            "items": [
                self._session_state(row, input_videos_by_session.get(row.id))
                for row in items
            ],
            "next_cursor": (
                _encode_cursor(items[-1].updated_at, items[-1].id)
                if has_more and items
                else None
            ),
        }

    async def create(
        self,
        *,
        editing_session_id: str,
        prompt: str,
        filename: str,
        max_clips: int = 8,
        asset_policy: str = "auto",
        max_generated_assets_per_clip: int = 2,
        stock_policy: str = "off",
        max_stock_assets_per_clip: int = 0,
        stock_asset_kind: str = "video",
        job_id: str | None = None,
    ) -> dict[str, Any]:
        clean_prompt = str(prompt or "").strip()
        if not clean_prompt:
            raise JobStoreError("PROMPT_REQUIRED", "an editing prompt is required")
        if not 1 <= int(max_clips) <= 50:
            raise JobStoreError("MAX_CLIPS_INVALID", "max_clips must be between 1 and 50")
        try:
            normalized_asset_policy = validate_asset_policy(asset_policy)
            generated_asset_limit = validate_generated_asset_limit(
                max_generated_assets_per_clip
            )
            normalized_stock_policy = validate_stock_policy(stock_policy)
            stock_asset_limit = validate_stock_asset_limit(max_stock_assets_per_clip)
            normalized_stock_kind = validate_stock_asset_kind(stock_asset_kind)
        except EditPlanError as exc:
            raise JobStoreError(exc.code, str(exc)) from exc
        if normalized_asset_policy == "required" and generated_asset_limit == 0:
            raise JobStoreError(
                "REQUIRED_GENERATED_ASSET_COUNT_INVALID",
                "required generated images need a positive per-clip count",
            )
        if normalized_stock_policy == "required" and stock_asset_limit == 0:
            raise JobStoreError(
                "REQUIRED_STOCK_ASSET_COUNT_INVALID",
                "required stock media needs a positive per-clip count",
            )
        editing_session = await self.get_session(editing_session_id)
        workflow_version = int(editing_session["workflow_version"])
        identifier = job_id or uuid.uuid4().hex
        if not JOB_ID_PATTERN.fullmatch(identifier):
            raise JobStoreError("JOB_ID_INVALID", "invalid job id")
        job_dir = self._prepare_job_directories(identifier)
        now = _utcnow()
        request_data = {
            "max_clips": int(max_clips),
            "asset_policy": normalized_asset_policy,
            "max_generated_assets_per_clip": generated_asset_limit,
            "stock_policy": normalized_stock_policy,
            "max_stock_assets_per_clip": stock_asset_limit,
            "stock_asset_kind": normalized_stock_kind,
        }
        if workflow_version == 1:
            # This method remains only for bounded historical import/test fixtures.
            request_data["edit_mode"] = "legacy"
        row = VideoJob(
            id=identifier,
            editing_session_id=editing_session_id,
            state="uploading",
            progress=Decimal("0"),
            prompt=clean_prompt[:12000],
            request_data=request_data,
            input_data={
                "original_filename": _safe_filename(filename),
                "stored_filename": "",
                "size": 0,
            },
            result_data={},
            updated_at=now,
            media_expires_at=now + self.media_retention,
            audit_expires_at=now + self.audit_retention,
        )
        try:
            async with self.database.sessions() as session:
                async with session.begin():
                    await session.execute(
                        text("SELECT pg_advisory_xact_lock(:key)"),
                        {"key": CAPACITY_ADVISORY_LOCK},
                    )
                    active = await session.scalar(
                        select(func.count())
                        .select_from(VideoJob)
                        .where(VideoJob.state.in_(ACTIVE_STATES))
                    )
                    if int(active or 0) >= self.max_active_jobs:
                        raise JobStoreError("JOB_QUEUE_FULL", "job queue is at capacity")
                    owner = await session.get(EditingSession, editing_session_id)
                    if (
                        owner is None
                        or owner.deleted_at is not None
                        or owner.audit_expires_at <= now
                    ):
                        raise JobStoreError("SESSION_NOT_FOUND", "session not found")
                    owner.updated_at = now
                    owner.audit_expires_at = now + self.audit_retention
                    session.add(row)
                    await session.flush()
                    await self._append_event(
                        session,
                        row,
                        "job_created",
                        {"state": "uploading"},
                    )
        except JobStoreError:
            shutil.rmtree(job_dir, ignore_errors=True)
            raise
        except IntegrityError:
            shutil.rmtree(job_dir, ignore_errors=True)
            raise JobStoreError("JOB_ALREADY_EXISTS", "job already exists") from None
        except SQLAlchemyError:
            shutil.rmtree(job_dir, ignore_errors=True)
            raise JobStoreError("DATABASE_UNAVAILABLE", "job storage is unavailable") from None
        state = await self.load(identifier)
        await self._snapshot(state)
        return state

    async def load(self, job_id: str) -> dict[str, Any]:
        return await self._load(job_id, include_deleted=False)

    async def load_for_audit(self, job_id: str) -> dict[str, Any]:
        return await self._load(job_id, include_deleted=True)

    async def _load(self, job_id: str, *, include_deleted: bool) -> dict[str, Any]:
        if not JOB_ID_PATTERN.fullmatch(str(job_id or "")):
            raise JobStoreError("JOB_ID_INVALID", "invalid job id")
        try:
            async with self.database.sessions() as session:
                row = await session.get(VideoJob, job_id)
                if row is None or (row.deleted_at is not None and not include_deleted):
                    raise JobStoreError("JOB_NOT_FOUND", "job not found")
                artifacts = list(
                    (
                        await session.execute(
                            select(Artifact)
                            .where(Artifact.job_id == job_id)
                            .order_by(Artifact.created_at, Artifact.id)
                        )
                    ).scalars()
                )
        except JobStoreError:
            raise
        except SQLAlchemyError:
            raise JobStoreError("DATABASE_UNAVAILABLE", "job storage is unavailable") from None
        return self._job_state(row, artifacts)

    async def list_jobs(
        self,
        editing_session_id: str,
        *,
        limit: int = 20,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        await self.get_session(editing_session_id)
        if not 1 <= int(limit) <= 50:
            raise JobStoreError("PAGE_LIMIT_INVALID", "limit must be between 1 and 50")
        boundary = _decode_cursor(cursor)
        query = (
            select(VideoJob)
            .where(
                VideoJob.editing_session_id == editing_session_id,
                VideoJob.deleted_at.is_(None),
            )
            .order_by(VideoJob.created_at.desc(), VideoJob.id.desc())
            .limit(int(limit) + 1)
        )
        if boundary:
            timestamp, item_id = boundary
            query = query.where(
                or_(
                    VideoJob.created_at < timestamp,
                    and_(VideoJob.created_at == timestamp, VideoJob.id < item_id),
                )
            )
        try:
            async with self.database.sessions() as session:
                rows = list((await session.execute(query)).scalars())
                selected = rows[: int(limit)]
                artifacts_by_job: dict[str, list[Artifact]] = {row.id: [] for row in selected}
                if selected:
                    artifacts = list(
                        (
                            await session.execute(
                                select(Artifact)
                                .where(Artifact.job_id.in_(artifacts_by_job))
                                .order_by(Artifact.created_at, Artifact.id)
                            )
                        ).scalars()
                    )
                    for artifact in artifacts:
                        artifacts_by_job[artifact.job_id].append(artifact)
        except SQLAlchemyError:
            raise JobStoreError("DATABASE_UNAVAILABLE", "job storage is unavailable") from None
        has_more = len(rows) > int(limit)
        return {
            "items": [self._job_state(row, artifacts_by_job[row.id]) for row in selected],
            "next_cursor": (
                _encode_cursor(selected[-1].created_at, selected[-1].id)
                if has_more and selected
                else None
            ),
        }

    async def update(
        self,
        job_id: str,
        *,
        event_type: str | None = None,
        **changes: Any,
    ) -> dict[str, Any]:
        now = _utcnow()
        try:
            async with self.database.sessions() as session:
                async with session.begin():
                    row = await session.scalar(
                        select(VideoJob)
                        .where(VideoJob.id == job_id, VideoJob.deleted_at.is_(None))
                        .with_for_update()
                    )
                    if row is None:
                        raise JobStoreError("JOB_NOT_FOUND", "job not found")
                    previous_state = row.state
                    result_data = dict(row.result_data or {})
                    for name, value in changes.items():
                        if name == "state":
                            row.state = str(value)
                        elif name == "stage":
                            row.stage = str(value) if value is not None else None
                        elif name == "progress":
                            row.progress = Decimal(str(value))
                        elif name == "error":
                            row.error_data = sanitize_for_persistence(value)
                        elif name == "request":
                            row.request_data = sanitize_for_persistence(value)
                        elif name == "input":
                            row.input_data = sanitize_for_persistence(value)
                        elif name == "recovery_count":
                            row.recovery_count = int(value)
                        else:
                            result_data[name] = sanitize_for_persistence(value)
                    row.result_data = result_data
                    row.updated_at = now
                    row.version += 1
                    if row.state == "running" and row.started_at is None:
                        row.started_at = now
                    if row.state in TERMINAL_STATES:
                        if previous_state not in TERMINAL_STATES or row.completed_at is None:
                            row.completed_at = now
                            row.media_expires_at = now + self.media_retention
                            row.audit_expires_at = now + self.audit_retention
                            owner = await session.get(EditingSession, row.editing_session_id)
                            if owner is not None and owner.audit_expires_at < row.audit_expires_at:
                                owner.audit_expires_at = row.audit_expires_at
                                owner.updated_at = now
                            if row.prompt_version_id is not None:
                                source = await session.scalar(
                                    select(SessionInputVideo)
                                    .where(
                                        SessionInputVideo.editing_session_id
                                        == row.editing_session_id,
                                        SessionInputVideo.state == "ready",
                                        SessionInputVideo.purged_at.is_(None),
                                    )
                                    .with_for_update()
                                )
                                if source is not None:
                                    source.expires_at = now + self.media_retention
                                    source.updated_at = now
                            artifacts = list(
                                (
                                    await session.execute(
                                        select(Artifact)
                                        .where(
                                            Artifact.job_id == row.id,
                                            Artifact.availability == "available",
                                        )
                                        .with_for_update()
                                    )
                                ).scalars()
                            )
                            for artifact in artifacts:
                                artifact.retention_expires_at = self._artifact_expiry(
                                    row,
                                    kind=artifact.kind,
                                    relative_path=artifact.relative_path,
                                )
                    derived_event = event_type or (
                        "job_state_changed"
                        if "state" in changes
                        else "job_stage_changed"
                        if "stage" in changes
                        else "job_updated"
                    )
                    await self._append_event(session, row, derived_event, changes)
        except JobStoreError:
            raise
        except (IntegrityError, SQLAlchemyError):
            raise JobStoreError("DATABASE_UNAVAILABLE", "job update failed") from None
        state = await self.load(job_id)
        await self._snapshot(state)
        return state

    def input_path(self, job_id: str, original_filename: str) -> Path:
        suffix = Path(_safe_filename(original_filename)).suffix.lower() or ".mp4"
        return self._job_dir(job_id) / "input" / f"source{suffix}"

    async def mark_uploaded(self, job_id: str, path: Path, size: int) -> dict[str, Any]:
        expected = self._job_dir(job_id) / "input"
        resolved = path.resolve()
        if expected.resolve() not in resolved.parents or not resolved.is_file():
            raise JobStoreError(
                "UPLOAD_PATH_INVALID",
                "uploaded file is outside the job input directory",
            )
        state = await self.load(job_id)
        input_info = dict(state.get("input") or {})
        input_info.update({"stored_filename": path.name, "size": int(size)})
        return await self.update(
            job_id,
            input=input_info,
            state="queued",
            progress=0.05,
            error=None,
            event_type="job_uploaded",
        )

    async def fail(
        self,
        job_id: str,
        *,
        code: str,
        message: str,
        details: Any = None,
    ) -> dict[str, Any]:
        error: dict[str, Any] = {
            "code": sanitize_text(code, limit=200),
            "message": sanitize_text(message, limit=1200),
        }
        if details is not None:
            error["details"] = sanitize_for_persistence(details)
        blocker_codes = (
            details.get("blocker_codes")
            if isinstance(details, dict) and isinstance(details.get("blocker_codes"), list)
            else []
        )
        technical_blocker_codes = (
            details.get("technical_blocker_codes")
            if isinstance(details, dict)
            and isinstance(details.get("technical_blocker_codes"), list)
            else []
        )
        creative_limitation_codes = (
            details.get("creative_limitation_codes")
            if isinstance(details, dict)
            and isinstance(details.get("creative_limitation_codes"), list)
            else []
        )
        state_before_failure = await self.load(job_id)
        repair_report: dict[str, Any] | None = None
        fallback_ledger: dict[str, Any] | None = None
        for name in ("repair_report.json", "fallback_ledger.json"):
            path = self.output_dir(job_id) / name
            try:
                if not path.is_file() or path.stat().st_size > 512_000:
                    continue
                raw_text = await asyncio.to_thread(path.read_text, encoding="utf-8")
                parsed = json.loads(raw_text)
                if not isinstance(parsed, dict):
                    continue
                if name == "repair_report.json":
                    repair_report = validate_repair_report(parsed)
                else:
                    fallback_ledger = parsed
            except (OSError, json.JSONDecodeError, RepairContractError):
                continue
        checkpoint_summary = (
            repair_report.get("checkpoints")
            if isinstance(repair_report, dict)
            and isinstance(repair_report.get("checkpoints"), dict)
            else None
        )
        rollout_attribution = (
            repair_report.get("attribution")
            if isinstance(repair_report, dict)
            and isinstance(repair_report.get("attribution"), dict)
            else None
        )
        outcome = build_failed_outcome_report(
            code=code,
            stage=state_before_failure.get("stage"),
            retryable=retryable_error(code),
            blocker_codes=blocker_codes,
            technical_blocker_codes=technical_blocker_codes,
            creative_limitation_codes=creative_limitation_codes,
            repair_report=repair_report,
            rollout_attribution=rollout_attribution,
            checkpoint_summary=checkpoint_summary,
            fallback_ledger=fallback_ledger,
        )
        state = await self.update(
            job_id,
            state="failed",
            error=error,
            outcome=outcome,
            event_type="job_failed",
        )
        failure_path = self.output_dir(job_id) / "failure.json"
        failure = sanitize_for_persistence(
            {
                "job_id": job_id,
                "state": "failed",
                "stage": state.get("stage"),
                "error": error,
                "created_at": state.get("created_at"),
                "failed_at": state.get("updated_at"),
            }
        )
        await asyncio.to_thread(self._write_atomic, failure_path, failure)
        return await self.register_artifact(job_id, failure_path, kind="failure")

    def output_dir(self, job_id: str) -> Path:
        path = self._job_dir(job_id) / "output"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def work_dir(self, job_id: str) -> Path:
        path = self._job_dir(job_id) / "work"
        path.mkdir(parents=True, exist_ok=True)
        return path

    async def source_path(self, job_id: str) -> Path:
        state = await self.load(job_id)
        input_data = state.get("input") or {}
        if input_data.get("source_kind") == "session_input_video":
            return await self._session_source_path(state)
        filename = str(input_data.get("stored_filename") or "")
        if not filename or Path(filename).name != filename:
            raise JobStoreError("JOB_INPUT_MISSING", "job input is missing")
        path = (self._job_dir(job_id) / "input" / filename).resolve()
        input_dir = (self._job_dir(job_id) / "input").resolve()
        if input_dir not in path.parents or not path.is_file():
            raise JobStoreError("JOB_INPUT_MISSING", "job input is missing")
        return path

    async def _session_source_path(self, state: dict[str, Any]) -> Path:
        input_data = state.get("input") or {}
        source_id = str(input_data.get("input_video_id") or "")
        relative_value = str(input_data.get("relative_path") or "")
        expected_hash = str(input_data.get("sha256") or "")
        expected_size = int(input_data.get("size") or 0)
        if (
            not JOB_ID_PATTERN.fullmatch(source_id)
            or len(expected_hash) != 64
            or expected_size <= 0
        ):
            raise JobStoreError("SESSION_SOURCE_UNAVAILABLE", "session source is unavailable")
        try:
            async with self.database.sessions() as session:
                source = await session.scalar(
                    select(SessionInputVideo).where(
                        SessionInputVideo.id == source_id,
                        SessionInputVideo.editing_session_id
                        == state["editing_session_id"],
                    )
                )
        except SQLAlchemyError:
            raise JobStoreError(
                "DATABASE_UNAVAILABLE", "session source is unavailable"
            ) from None
        now = _utcnow()
        if (
            source is None
            or source.state != "ready"
            or source.purged_at is not None
            or source.expires_at is None
            or source.expires_at <= now
        ):
            raise JobStoreError("SESSION_SOURCE_UNAVAILABLE", "session source is unavailable")
        if (
            source.relative_path != relative_value
            or source.sha256 != expected_hash
            or source.expected_size != expected_size
            or source.received_bytes != expected_size
        ):
            raise JobStoreError("SESSION_SOURCE_CHANGED", "session source identity changed")
        relative = Path(relative_value)
        if (
            relative.is_absolute()
            or ".." in relative.parts
            or len(relative.parts) != 3
            or relative.parts[0] != state["editing_session_id"]
            or relative.parts[1] != "input"
            or not relative.parts[2].startswith("source.")
        ):
            raise JobStoreError("SESSION_SOURCE_PATH_INVALID", "session source path is invalid")
        path = self.session_media_root / relative
        current = self.session_media_root
        for part in relative.parts[:-1]:
            current /= part
            if current.is_symlink():
                raise JobStoreError(
                    "SESSION_SOURCE_PATH_INVALID", "session source path is invalid"
                )
        resolved = path.resolve(strict=False)
        if (
            self.session_media_root not in resolved.parents
            or path.is_symlink()
            or not path.is_file()
            or path.stat().st_size != expected_size
        ):
            raise JobStoreError("SESSION_SOURCE_UNAVAILABLE", "session source is unavailable")
        digest = await asyncio.to_thread(_sha256_file, path)
        if digest != expected_hash:
            raise JobStoreError("SESSION_SOURCE_CHANGED", "session source identity changed")
        return path

    async def register_artifact(
        self,
        job_id: str,
        path: str | Path,
        *,
        kind: str,
    ) -> dict[str, Any]:
        job_dir = self._job_dir(job_id).resolve()
        output_dir = (job_dir / "output").resolve()
        artifact_path = Path(path).resolve()
        if output_dir not in artifact_path.parents or not artifact_path.is_file():
            raise JobStoreError(
                "ARTIFACT_PATH_INVALID",
                "artifact is outside the job output directory",
            )
        relative_path = artifact_path.relative_to(job_dir).as_posix()
        digest = await asyncio.to_thread(_sha256_file, artifact_path)
        now = _utcnow()
        try:
            async with self.database.sessions() as session:
                async with session.begin():
                    job = await session.scalar(
                        select(VideoJob).where(VideoJob.id == job_id).with_for_update()
                    )
                    if job is None or job.deleted_at is not None:
                        raise JobStoreError("JOB_NOT_FOUND", "job not found")
                    artifact = await session.scalar(
                        select(Artifact)
                        .where(Artifact.job_id == job_id, Artifact.name == artifact_path.name)
                        .with_for_update()
                    )
                    if artifact is None:
                        artifact = Artifact(
                            job_id=job_id,
                            name=artifact_path.name,
                            kind=str(kind)[:64],
                            relative_path=relative_path,
                            mime_type=None,
                            size=artifact_path.stat().st_size,
                            sha256=digest,
                            availability="available",
                            retention_expires_at=self._artifact_expiry(
                                job,
                                kind=str(kind)[:64],
                                relative_path=relative_path,
                            ),
                        )
                        session.add(artifact)
                    else:
                        artifact.kind = str(kind)[:64]
                        artifact.relative_path = relative_path
                        artifact.size = artifact_path.stat().st_size
                        artifact.sha256 = digest
                        artifact.availability = "available"
                        artifact.retention_expires_at = self._artifact_expiry(
                            job,
                            kind=artifact.kind,
                            relative_path=relative_path,
                        )
                        artifact.purged_at = None
                        artifact.purge_reason = None
                    job.updated_at = now
                    job.version += 1
                    await self._append_event(
                        session,
                        job,
                        "artifact_registered",
                        {"name": artifact_path.name, "kind": str(kind)[:64]},
                    )
        except JobStoreError:
            raise
        except SQLAlchemyError:
            raise JobStoreError("DATABASE_UNAVAILABLE", "artifact storage is unavailable") from None
        state = await self.load(job_id)
        await self._snapshot(state)
        if self.audit is not None and artifact_path.suffix.lower() in {".json", ".srt"}:
            await self._audit_safely(
                job_id,
                "ingest_artifact",
                lambda: self.audit.ingest_artifact(job_id, artifact_path.name),
            )
        return state

    async def resolve_artifact(self, job_id: str, name: str) -> Path:
        return await self._resolve_artifact(job_id, name, include_deleted=False)

    async def resolve_artifact_for_audit(self, job_id: str, name: str) -> Path:
        return await self._resolve_artifact(job_id, name, include_deleted=True)

    async def _resolve_artifact(
        self,
        job_id: str,
        name: str,
        *,
        include_deleted: bool,
    ) -> Path:
        clean_name = Path(str(name or "").replace("\\", "/")).name
        if not clean_name or clean_name != name:
            raise JobStoreError("ARTIFACT_NOT_FOUND", "artifact not found")
        try:
            async with self.database.sessions() as session:
                artifact = await session.scalar(
                    select(Artifact)
                    .join(VideoJob, VideoJob.id == Artifact.job_id)
                    .where(
                        Artifact.job_id == job_id,
                        Artifact.name == clean_name,
                        Artifact.availability == "available",
                        True if include_deleted else VideoJob.deleted_at.is_(None),
                    )
                )
        except SQLAlchemyError:
            raise JobStoreError("DATABASE_UNAVAILABLE", "artifact storage is unavailable") from None
        if artifact is None:
            raise JobStoreError("ARTIFACT_NOT_FOUND", "artifact not found")
        relative = Path(artifact.relative_path)
        if relative.is_absolute() or ".." in relative.parts:
            raise JobStoreError("ARTIFACT_NOT_FOUND", "artifact not found")
        job_dir = self._job_dir(job_id).resolve()
        path = (job_dir / relative).resolve()
        if job_dir not in path.parents or path.name != clean_name or not path.is_file():
            raise JobStoreError("ARTIFACT_NOT_FOUND", "artifact not found")
        return path

    async def build_bundle(self, job_id: str) -> Path:
        state = await self.load(job_id)
        paths: list[tuple[Path, str]] = []
        for artifact in state.get("artifacts", []):
            name = str(artifact.get("name") or "")
            try:
                paths.append((await self.resolve_artifact(job_id, name), name))
            except JobStoreError:
                continue
        destination = self.work_dir(job_id) / f"{job_id}-artifacts.zip"

        def write_bundle() -> None:
            temporary = destination.with_suffix(".tmp")
            temporary.unlink(missing_ok=True)
            try:
                with zipfile.ZipFile(
                    temporary,
                    "w",
                    compression=zipfile.ZIP_DEFLATED,
                ) as bundle:
                    for path, name in paths:
                        bundle.write(path, arcname=name)
                os.replace(temporary, destination)
            finally:
                temporary.unlink(missing_ok=True)

        await asyncio.to_thread(write_bundle)
        return destination

    async def claim_next_job(self) -> str | None:
        now = _utcnow()
        job_id: str | None = None
        try:
            async with self.database.sessions() as session:
                async with session.begin():
                    row = await session.scalar(
                        select(VideoJob)
                        .join(
                            EditingSession,
                            EditingSession.id == VideoJob.editing_session_id,
                        )
                        .where(
                            VideoJob.state == "queued",
                            VideoJob.deleted_at.is_(None),
                            EditingSession.workflow_version == 2,
                            EditingSession.deleted_at.is_(None),
                        )
                        .order_by(VideoJob.created_at, VideoJob.id)
                        .limit(1)
                        .with_for_update(skip_locked=True)
                    )
                    if row is None:
                        return None
                    row.state = "running"
                    row.stage = "starting"
                    row.progress = max(row.progress, Decimal("0.1"))
                    row.error_data = None
                    row.started_at = row.started_at or now
                    row.updated_at = now
                    row.version += 1
                    await self._append_event(session, row, "job_started", {"state": "running"})
                    job_id = row.id
        except SQLAlchemyError:
            raise JobStoreError("DATABASE_UNAVAILABLE", "job queue is unavailable") from None
        if job_id:
            state = await self.load(job_id)
            await self._snapshot(state)
            await ActivityService(self).emit_safely(
                job_id,
                stage="starting",
                category="system",
                status="started",
                message_key="activity.system.starting",
                progress=float(state["progress"]),
            )
        return job_id

    async def recover_pending(self, *, limit: int = 100) -> list[str]:
        now = _utcnow()
        recovered: list[str] = []
        try:
            async with self.database.sessions() as session:
                async with session.begin():
                    rows = list(
                        (
                            await session.execute(
                                select(VideoJob)
                                .join(
                                    EditingSession,
                                    EditingSession.id == VideoJob.editing_session_id,
                                )
                                .where(
                                    VideoJob.state.in_({"queued", "running"}),
                                    VideoJob.deleted_at.is_(None),
                                    EditingSession.workflow_version == 2,
                                    EditingSession.deleted_at.is_(None),
                                )
                                .order_by(VideoJob.created_at, VideoJob.id)
                                .limit(limit)
                                .with_for_update(skip_locked=True)
                            )
                        ).scalars()
                    )
                    for row in rows:
                        row.state = "queued"
                        row.recovery_count += 1
                        row.updated_at = now
                        row.version += 1
                        await self._append_event(
                            session,
                            row,
                            "job_recovered",
                            {"state": "queued", "recovery_count": row.recovery_count},
                        )
                        recovered.append(row.id)
        except SQLAlchemyError:
            raise JobStoreError("DATABASE_UNAVAILABLE", "job recovery is unavailable") from None
        for job_id in recovered:
            state = await self.load(job_id)
            await self._snapshot(state)
            await ActivityService(self).emit_safely(
                job_id,
                stage=state.get("stage"),
                category="queue",
                status="warning",
                message_key="activity.queue.recovered",
                progress=float(state["progress"]),
            )
        return recovered

    async def events(
        self,
        job_id: str,
        *,
        limit: int = 200,
        include_deleted: bool = False,
    ) -> list[dict[str, Any]]:
        if not 1 <= int(limit) <= 500:
            raise JobStoreError("PAGE_LIMIT_INVALID", "event limit is invalid")
        await (self.load_for_audit(job_id) if include_deleted else self.load(job_id))
        try:
            async with self.database.sessions() as session:
                rows = list(
                    (
                        await session.execute(
                            select(JobEvent)
                            .where(JobEvent.job_id == job_id)
                            .order_by(JobEvent.sequence)
                            .limit(int(limit))
                        )
                    ).scalars()
                )
        except SQLAlchemyError:
            raise JobStoreError("DATABASE_UNAVAILABLE", "job events are unavailable") from None
        return [
            {
                "sequence": row.sequence,
                "event_type": row.event_type,
                "audience": row.audience,
                "state": row.state,
                "stage": row.stage,
                "payload": row.payload,
                "occurred_at": _iso(row.occurred_at),
            }
            for row in rows
        ]

    async def public_events(
        self,
        job_id: str,
        *,
        after_sequence: int,
        limit: int,
    ) -> tuple[list[dict[str, Any]], str]:
        state = await self.load(job_id)
        try:
            async with self.database.sessions() as session:
                rows = list(
                    (
                        await session.execute(
                            select(JobEvent)
                            .where(
                                JobEvent.job_id == job_id,
                                JobEvent.audience == "user",
                                JobEvent.sequence > int(after_sequence),
                            )
                            .order_by(JobEvent.sequence)
                            .limit(int(limit))
                        )
                    ).scalars()
                )
        except SQLAlchemyError:
            raise JobStoreError("DATABASE_UNAVAILABLE", "job activity is unavailable") from None
        events = []
        for row in rows:
            payload = dict(row.payload or {})
            payload.update(
                {
                    "sequence": row.sequence,
                    "stage": row.stage,
                    "occurred_at": _iso(row.occurred_at),
                }
            )
            events.append(payload)
        return events, str(state["state"])

    async def record_public_event(
        self,
        job_id: str,
        *,
        stage: str | None,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        try:
            async with self.database.sessions() as session:
                async with session.begin():
                    row = await session.scalar(
                        select(VideoJob)
                        .where(VideoJob.id == job_id, VideoJob.deleted_at.is_(None))
                        .with_for_update()
                    )
                    if row is None:
                        raise JobStoreError("JOB_NOT_FOUND", "job not found")
                    recent = list(
                        (
                            await session.execute(
                                select(JobEvent)
                                .where(
                                    JobEvent.job_id == job_id,
                                    JobEvent.audience == "user",
                                )
                                .order_by(JobEvent.sequence.desc())
                                .limit(100)
                            )
                        ).scalars()
                    )
                    previous_progress = next(
                        (
                            float((event.payload or {})["progress"])
                            for event in recent
                            if (event.payload or {}).get("progress") is not None
                        ),
                        None,
                    )
                    progress = payload.get("progress")
                    if (
                        progress is not None
                        and previous_progress is not None
                        and float(progress) < previous_progress
                    ):
                        raise JobStoreError(
                            "ACTIVITY_PROGRESS_INVALID",
                            "public activity progress cannot decrease",
                        )
                    event = await self._append_event(
                        session,
                        row,
                        "public_activity",
                        payload,
                        audience="user",
                        stage=stage,
                    )
        except JobStoreError:
            raise
        except (IntegrityError, SQLAlchemyError):
            raise JobStoreError(
                "DATABASE_UNAVAILABLE", "job activity storage is unavailable"
            ) from None
        return {
            **dict(event.payload or {}),
            "sequence": event.sequence,
            "stage": event.stage,
            "occurred_at": _iso(event.occurred_at),
        }

    async def record_event(
        self,
        job_id: str,
        event_type: str,
        payload: Any,
    ) -> None:
        try:
            async with self.database.sessions() as session:
                async with session.begin():
                    row = await session.scalar(
                        select(VideoJob)
                        .where(VideoJob.id == job_id, VideoJob.deleted_at.is_(None))
                        .with_for_update()
                    )
                    if row is None:
                        raise JobStoreError("JOB_NOT_FOUND", "job not found")
                    await self._append_event(session, row, event_type, payload)
        except JobStoreError:
            raise
        except SQLAlchemyError:
            raise JobStoreError("DATABASE_UNAVAILABLE", "job event storage is unavailable") from None

    async def import_legacy_jobs(
        self,
        legacy_root: str | Path,
        *,
        dry_run: bool,
        batch_size: int = 100,
        session_title: str = "Imported legacy jobs",
    ) -> dict[str, int]:
        root = Path(legacy_root).expanduser().resolve()
        if not root.is_dir():
            raise JobStoreError("IMPORT_ROOT_INVALID", "legacy root is not a directory")
        if not 1 <= int(batch_size) <= 1000:
            raise JobStoreError("IMPORT_BATCH_INVALID", "batch size must be between 1 and 1000")
        report = {
            "scanned": 0,
            "imported": 0,
            "would_import": 0,
            "already_present": 0,
            "invalid": 0,
            "artifacts": 0,
            "missing_artifacts": 0,
            "unsafe_artifacts": 0,
        }
        candidates = [path for path in sorted(root.iterdir()) if path.is_dir()][
            : int(batch_size)
        ]
        import_session_id = uuid.uuid5(uuid.NAMESPACE_URL, f"legacy:{root}").hex
        session_ready = False
        for job_dir in candidates:
            report["scanned"] += 1
            job_id = job_dir.name
            if not JOB_ID_PATTERN.fullmatch(job_id):
                report["invalid"] += 1
                continue
            state_path = job_dir / "job.json"
            try:
                if state_path.stat().st_size > 2 * 1024 * 1024:
                    raise ValueError("oversized")
                state = json.loads(state_path.read_text(encoding="utf-8"))
            except (OSError, ValueError, json.JSONDecodeError):
                report["invalid"] += 1
                continue
            if not isinstance(state, dict) or state.get("id") != job_id:
                report["invalid"] += 1
                continue
            try:
                await self.load(job_id)
            except JobStoreError as exc:
                if exc.code != "JOB_NOT_FOUND":
                    raise
            else:
                report["already_present"] += 1
                continue
            artifacts, missing, unsafe = await asyncio.to_thread(
                self._legacy_artifacts,
                job_dir,
                state,
            )
            report["artifacts"] += len(artifacts)
            report["missing_artifacts"] += missing
            report["unsafe_artifacts"] += unsafe
            if dry_run:
                report["would_import"] += 1
                continue
            if not session_ready:
                await self._ensure_import_session(import_session_id, session_title)
                session_ready = True
            await self._import_legacy_job(import_session_id, job_dir, state, artifacts)
            report["imported"] += 1
        return report

    async def _ensure_import_session(self, session_id: str, title: str) -> None:
        try:
            await self.get_session(session_id)
        except JobStoreError as exc:
            if exc.code != "SESSION_NOT_FOUND":
                raise
            await self.create_session(title, session_id=session_id)

    def _legacy_artifacts(
        self,
        job_dir: Path,
        state: dict[str, Any],
    ) -> tuple[list[dict[str, Any]], int, int]:
        output_dir = (job_dir / "output").resolve()
        named: dict[str, str] = {}
        unsafe = 0
        for item in list(state.get("artifacts") or [])[:500]:
            name = str((item or {}).get("name") or "")
            if not name or Path(name).name != name or "\\" in name:
                unsafe += 1
                continue
            named[name] = str((item or {}).get("kind") or "artifact")[:64]
        if output_dir.is_dir():
            for path in sorted(output_dir.iterdir())[:500]:
                if path.is_file() and path.suffix.lower() in {".json", ".srt"}:
                    named.setdefault(
                        path.name,
                        "subtitles" if path.suffix.lower() == ".srt" else "audit_json",
                    )
        artifacts: list[dict[str, Any]] = []
        missing = 0
        for name, kind in named.items():
            path = (output_dir / name).resolve()
            if output_dir not in path.parents:
                unsafe += 1
                continue
            if not path.is_file():
                missing += 1
                artifacts.append(
                    {
                        "name": name,
                        "kind": kind,
                        "relative_path": f"output/{name}",
                        "size": 0,
                        "sha256": None,
                        "availability": "missing",
                    }
                )
                continue
            artifacts.append(
                {
                    "name": name,
                    "kind": kind,
                    "relative_path": f"output/{name}",
                    "size": path.stat().st_size,
                    "sha256": _sha256_file(path),
                    "availability": "available",
                }
            )
        return artifacts, missing, unsafe

    async def _import_legacy_job(
        self,
        editing_session_id: str,
        job_dir: Path,
        state: dict[str, Any],
        artifacts: list[dict[str, Any]],
    ) -> None:
        now = _utcnow()
        created_at = _parse_legacy_time(state.get("created_at"), now)
        updated_at = _parse_legacy_time(state.get("updated_at"), created_at)
        state_name = str(state.get("state") or "failed")
        if state_name not in ACTIVE_STATES | TERMINAL_STATES:
            state_name = "failed"
        prompt = sanitize_text(state.get("prompt"), limit=12000).strip() or "Imported legacy job"
        known = {
            "id",
            "state",
            "stage",
            "progress",
            "prompt",
            "prompt_version_id",
            "attempt_number",
            "is_favorite",
            "request",
            "input",
            "artifacts",
            "error",
            "created_at",
            "updated_at",
            "recovery_count",
        }
        result_data = sanitize_for_persistence(
            {key: value for key, value in state.items() if key not in known}
        )
        try:
            progress = Decimal(str(state.get("progress", 0)))
        except Exception:
            progress = Decimal("0")
        progress = min(Decimal("1"), max(Decimal("0"), progress))
        try:
            recovery_count = max(0, int(state.get("recovery_count") or 0))
        except (TypeError, ValueError):
            recovery_count = 0
        row = VideoJob(
            id=str(state["id"]),
            editing_session_id=editing_session_id,
            state=state_name,
            stage=sanitize_text(state.get("stage"), limit=64) or None,
            progress=progress,
            prompt=prompt,
            request_data=sanitize_for_persistence(state.get("request") or {}),
            input_data=sanitize_for_persistence(state.get("input") or {}),
            error_data=sanitize_for_persistence(state.get("error")),
            result_data=result_data,
            recovery_count=recovery_count,
            created_at=created_at,
            updated_at=updated_at,
            started_at=created_at if state_name in {"running", *TERMINAL_STATES} else None,
            completed_at=updated_at if state_name in TERMINAL_STATES else None,
            media_expires_at=now + self.media_retention,
            audit_expires_at=now + self.audit_retention,
        )
        try:
            async with self.database.sessions() as session:
                async with session.begin():
                    session.add(row)
                    await session.flush()
                    for item in artifacts:
                        session.add(
                            Artifact(
                                job_id=row.id,
                                name=item["name"],
                                kind=item["kind"],
                                relative_path=item["relative_path"],
                                size=item["size"],
                                sha256=item["sha256"],
                                availability=item["availability"],
                                retention_expires_at=self._artifact_expiry(
                                    row,
                                    kind=item["kind"],
                                    relative_path=item["relative_path"],
                                ),
                            )
                        )
                    await self._append_event(
                        session,
                        row,
                        "legacy_job_imported",
                        {"artifact_count": len(artifacts), "source": "filesystem"},
                    )
        except IntegrityError:
            return
        except SQLAlchemyError:
            raise JobStoreError("DATABASE_UNAVAILABLE", "legacy import failed") from None
        await self._snapshot(await self.load(row.id))

    async def _append_event(
        self,
        session: AsyncSession,
        row: VideoJob,
        event_type: str,
        payload: Any,
        *,
        audience: str = "internal",
        stage: str | None = None,
    ) -> JobEvent:
        sequence = await session.scalar(
            select(func.coalesce(func.max(JobEvent.sequence), 0)).where(
                JobEvent.job_id == row.id
            )
        )
        clean_payload = sanitize_for_persistence(payload)
        if not isinstance(clean_payload, dict):
            clean_payload = {"value": clean_payload}
        event = JobEvent(
            job_id=row.id,
            sequence=int(sequence or 0) + 1,
            event_type=sanitize_text(event_type, limit=80),
            audience=audience,
            state=row.state,
            stage=stage if stage is not None else row.stage,
            payload=clean_payload,
        )
        session.add(event)
        await session.flush()
        emit_event(
            event_type,
            editing_session_id=row.editing_session_id,
            job_id=row.id,
            stage=row.stage,
            outcome=row.state,
            error_code=(
                str((row.error_data or {}).get("code") or "") or None
                if isinstance(row.error_data, dict)
                else None
            ),
        )
        return event

    async def _snapshot(self, state: dict[str, Any]) -> None:
        try:
            await asyncio.to_thread(self._write_atomic, self._state_path(state["id"]), state)
        except OSError:
            try:
                async with self.database.sessions() as session:
                    async with session.begin():
                        row = await session.scalar(
                            select(VideoJob).where(VideoJob.id == state["id"]).with_for_update()
                        )
                        if row is not None:
                            await self._append_event(
                                session,
                                row,
                                "snapshot_write_failed",
                                {"code": "JOB_SNAPSHOT_WRITE_FAILED"},
                            )
            except SQLAlchemyError:
                pass

    @staticmethod
    def _session_state(
        row: EditingSession,
        input_video: SessionInputVideo | None,
    ) -> dict[str, Any]:
        return {
            "id": row.id,
            "title": row.title,
            "workflow_version": row.workflow_version,
            "input_video": (
                {
                    "id": input_video.id,
                    "state": input_video.state,
                    "original_filename": input_video.original_filename,
                    "expected_size": input_video.expected_size,
                    "received_bytes": input_video.received_bytes,
                    "media_type": input_video.media_type,
                    "sha256": input_video.sha256,
                    "completed_at": _iso(input_video.completed_at),
                    "expires_at": _iso(input_video.expires_at),
                    "purged_at": _iso(input_video.purged_at),
                }
                if input_video is not None
                else None
            ),
            "created_at": _iso(row.created_at),
            "updated_at": _iso(row.updated_at),
            "deleted_at": _iso(row.deleted_at),
        }

    @staticmethod
    def _job_state(row: VideoJob, artifacts: list[Artifact]) -> dict[str, Any]:
        state: dict[str, Any] = {
            "id": row.id,
            "editing_session_id": row.editing_session_id,
            "prompt_version_id": row.prompt_version_id,
            "attempt_number": row.attempt_number,
            "is_favorite": row.is_favorite,
            "state": row.state,
            "stage": row.stage,
            "progress": float(row.progress),
            "prompt": row.prompt,
            "request": dict(row.request_data or {}),
            "input": dict(row.input_data or {}),
            "artifacts": [
                {
                    "name": artifact.name,
                    "kind": artifact.kind,
                    "size": artifact.size,
                    "availability": artifact.availability,
                    "retention_expires_at": _iso(artifact.retention_expires_at),
                    "purged_at": _iso(artifact.purged_at),
                    "purge_reason": artifact.purge_reason,
                }
                for artifact in artifacts
            ],
            "error": row.error_data,
            "created_at": _iso(row.created_at),
            "updated_at": _iso(row.updated_at),
            "started_at": _iso(row.started_at),
            "completed_at": _iso(row.completed_at),
            "deleted_at": _iso(row.deleted_at),
            "media_expires_at": _iso(row.media_expires_at),
            "audit_expires_at": _iso(row.audit_expires_at),
            "recovery_count": row.recovery_count,
            "version": row.version,
        }
        state.update(dict(row.result_data or {}))
        return state


class JobManager:
    def __init__(
        self,
        store: JobStore,
        processor: Optional[Processor] = None,
        *,
        poll_interval: float = 0.25,
    ) -> None:
        self.store = store
        self.processor = processor
        self.poll_interval = poll_interval
        self._stop = asyncio.Event()
        self._wake = asyncio.Event()
        self._coordinator: asyncio.Task[None] | None = None
        self._lock_connection: AsyncConnection | None = None
        self._execution_connection: AsyncConnection | None = None
        self.is_leader = False

    async def start(self) -> None:
        if self.processor is None:
            return
        if self._coordinator is None or self._coordinator.done():
            self._stop.clear()
            self._coordinator = asyncio.create_task(
                self._coordinate(),
                name="openstoryline-mvp-worker-coordinator",
            )

    async def enqueue(self, job_id: str) -> None:
        try:
            async with self.store.database.sessions() as session:
                workflow_version = await session.scalar(
                    select(EditingSession.workflow_version)
                    .join(
                        VideoJob,
                        VideoJob.editing_session_id == EditingSession.id,
                    )
                    .where(
                        VideoJob.id == job_id,
                        VideoJob.deleted_at.is_(None),
                        EditingSession.deleted_at.is_(None),
                    )
                )
        except SQLAlchemyError:
            raise JobStoreError(
                "DATABASE_UNAVAILABLE", "job queue is unavailable"
            ) from None
        if workflow_version is None:
            raise JobStoreError("JOB_NOT_FOUND", "job not found")
        if int(workflow_version) != 2:
            raise JobStoreError(
                "SESSION_WORKFLOW_LEGACY",
                "historical workflow jobs cannot be executed",
            )
        state = await self.store.load(job_id)
        if state.get("state") in TERMINAL_STATES:
            raise JobStoreError("JOB_TERMINAL", "terminal jobs cannot be queued")
        if state.get("state") != "queued":
            state = await self.store.update(job_id, state="queued", event_type="job_queued")
        await ActivityService(self.store).emit_safely(
            job_id,
            stage=state.get("stage") or "queued",
            category="queue",
            status="queued",
            message_key="activity.queue.waiting",
            progress=float(state["progress"]),
            attempt_number=state.get("attempt_number"),
        )
        self._wake.set()

    async def stop(self) -> None:
        if self._coordinator is None:
            return
        self._stop.set()
        self._wake.set()
        await self._coordinator
        self._coordinator = None

    async def wait_until_leader(self, *, timeout: float = 5.0) -> bool:
        deadline = asyncio.get_running_loop().time() + timeout
        while asyncio.get_running_loop().time() < deadline:
            if self.is_leader:
                return True
            await asyncio.sleep(0.05)
        return False

    async def wait_for_terminal(self, job_id: str, *, timeout: float = 5.0) -> dict[str, Any]:
        deadline = asyncio.get_running_loop().time() + timeout
        while asyncio.get_running_loop().time() < deadline:
            state = await self.store.load(job_id)
            if state.get("state") in TERMINAL_STATES:
                return state
            await asyncio.sleep(0.05)
        raise TimeoutError(f"job {job_id} did not reach a terminal state")

    async def _coordinate(self) -> None:
        while not self._stop.is_set():
            connection: AsyncConnection | None = None
            execution_connection: AsyncConnection | None = None
            try:
                connection = await self.store.database.engine.connect()
                acquired = await connection.scalar(
                    text("SELECT pg_try_advisory_lock(:key)"),
                    {"key": WORKER_ADVISORY_LOCK},
                )
                if not acquired:
                    await connection.close()
                    await self._wait_for_wake()
                    continue
                execution_connection = await self.store.database.engine.connect()
                execution_acquired = await execution_connection.scalar(
                    text("SELECT pg_try_advisory_lock(:key)"),
                    {"key": WORKER_EXECUTION_ADVISORY_LOCK},
                )
                if not execution_acquired:
                    await asyncio.sleep(self.poll_interval)
                    continue
                self._lock_connection = connection
                self._execution_connection = execution_connection
                self.is_leader = True
                await self.store.recover_pending()
                await self._leader_loop()
            except (SQLAlchemyError, JobStoreError):
                await asyncio.sleep(self.poll_interval)
            finally:
                self.is_leader = False
                self._lock_connection = None
                self._execution_connection = None
                if execution_connection is not None:
                    await self._release_lock(
                        execution_connection,
                        WORKER_EXECUTION_ADVISORY_LOCK,
                    )
                if connection is not None:
                    await self._release_lock(connection, WORKER_ADVISORY_LOCK)
            if not self._stop.is_set():
                await self._wait_for_wake()

    async def _leader_loop(self) -> None:
        while not self._stop.is_set():
            if not await self._locks_alive():
                return
            job_id = await self.store.claim_next_job()
            if job_id is None:
                await self._wait_for_wake()
                continue
            if not await self._process_with_lock_monitor(job_id):
                return

    async def _process_with_lock_monitor(self, job_id: str) -> bool:
        task = asyncio.create_task(self._process(job_id))
        leadership_lost = False
        while not task.done():
            done, _pending = await asyncio.wait({task}, timeout=self.poll_interval)
            if done:
                break
            if not await self._locks_alive():
                leadership_lost = True
                break
        await task
        return not leadership_lost

    async def _process(self, job_id: str) -> None:
        try:
            result = await self.processor(job_id, self.store) if self.processor else None
            current = await self.store.load(job_id)
            if current.get("state") not in TERMINAL_STATES:
                current = await self.store.update(
                    job_id,
                    state="completed",
                    progress=1.0,
                    event_type="job_completed",
                    **(result or {}),
                )
                await ActivityService(self.store).emit_safely(
                    job_id,
                    stage="completed",
                    category="system",
                    status="completed",
                    message_key="activity.system.completed",
                    progress=1.0,
                    clip_count=int(current.get("clip_count") or 0),
                )
            if self.store.audit is not None:
                await self.store._audit_safely(
                    job_id,
                    "ingest_job_snapshot",
                    lambda: self.store.audit.ingest_job_snapshot(job_id),
                )
                if current.get("state") == "completed":
                    await self.store._audit_safely(
                        job_id,
                        "verify_job",
                        lambda: self.store.audit.verify_job(job_id),
                    )
            await self.store._retention_safely(job_id)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            code = str(getattr(exc, "code", "JOB_PROCESSING_FAILED"))
            details = getattr(exc, "to_dict", lambda: None)()
            await self.store.fail(job_id, code=code, message=str(exc), details=details)
            failed = await self.store.load(job_id)
            await ActivityService(self.store).emit_safely(
                job_id,
                stage=failed.get("stage"),
                category="system",
                status="failed",
                message_key="activity.system.failed",
                progress=float(failed.get("progress") or 0),
                error_code=code,
                retryable=retryable_error(code),
            )
            if self.store.audit is not None:
                await self.store._audit_safely(
                    job_id,
                    "ingest_job_snapshot",
                    lambda: self.store.audit.ingest_job_snapshot(job_id),
                )
            await self.store._retention_safely(job_id)

    async def _locks_alive(self) -> bool:
        return await self._connection_alive(
            self._lock_connection
        ) and await self._connection_alive(self._execution_connection)

    @staticmethod
    async def _connection_alive(connection: AsyncConnection | None) -> bool:
        if connection is None or connection.closed:
            return False
        try:
            return bool(await connection.scalar(text("SELECT 1")))
        except SQLAlchemyError:
            return False

    @staticmethod
    async def _release_lock(connection: AsyncConnection, key: int) -> None:
        try:
            if not connection.closed:
                await connection.execute(
                    text("SELECT pg_advisory_unlock(:key)"),
                    {"key": key},
                )
        except SQLAlchemyError:
            pass
        await connection.close()

    async def _wait_for_wake(self) -> None:
        try:
            await asyncio.wait_for(self._wake.wait(), timeout=self.poll_interval)
        except TimeoutError:
            pass
        self._wake.clear()
