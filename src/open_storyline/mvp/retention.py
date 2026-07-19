from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, AsyncIterator, Callable
import asyncio
import os

from sqlalchemy import and_, exists, func, or_, select, text
from sqlalchemy.exc import SQLAlchemyError

from open_storyline.mvp.jobs import (
    ACTIVE_STATES,
    JOB_ID_PATTERN,
    TERMINAL_STATES,
    JobStore,
    JobStoreError,
    _iso,
)
from open_storyline.mvp.models import Artifact, EditingSession, SessionInputVideo, VideoJob
from open_storyline.mvp.observability import emit_event
from open_storyline.mvp.security import sanitize_text
from open_storyline.mvp.session_media import SessionMediaStore


CLEANUP_ADVISORY_LOCK = 7_303_110_792_764
MEDIA_SUFFIXES = {".avi", ".m4v", ".mkv", ".mov", ".mp4", ".webm", ".zip"}


def _bounded_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)).strip())
    except ValueError:
        raise JobStoreError("RETENTION_CONFIG_INVALID", f"{name} must be an integer") from None
    if not minimum <= value <= maximum:
        raise JobStoreError(
            "RETENTION_CONFIG_INVALID",
            f"{name} must be between {minimum} and {maximum}",
        )
    return value


def _boolean(name: str, default: bool) -> bool:
    raw = os.getenv(name, "true" if default else "false").strip().lower()
    if raw not in {"true", "false"}:
        raise JobStoreError("RETENTION_CONFIG_INVALID", f"{name} must be true or false")
    return raw == "true"


@dataclass(frozen=True)
class RetentionSettings:
    enabled: bool
    media_days: int
    audit_days: int
    interval_seconds: int
    batch_size: int
    incomplete_upload_hours: int = 24

    @classmethod
    def from_env(cls) -> "RetentionSettings":
        return cls(
            enabled=_boolean("OPENSTORYLINE_RETENTION_ENABLED", False),
            media_days=_bounded_int("OPENSTORYLINE_MEDIA_RETENTION_DAYS", 7, 1, 365),
            audit_days=_bounded_int("OPENSTORYLINE_AUDIT_RETENTION_DAYS", 30, 1, 3650),
            interval_seconds=_bounded_int(
                "OPENSTORYLINE_RETENTION_INTERVAL_SECONDS",
                86_400,
                3600,
                604_800,
            ),
            batch_size=_bounded_int("OPENSTORYLINE_RETENTION_BATCH_SIZE", 100, 1, 1000),
            incomplete_upload_hours=_bounded_int(
                "OPENSTORYLINE_INCOMPLETE_UPLOAD_HOURS", 24, 1, 168
            ),
        )


@dataclass(frozen=True)
class SourceRetentionCandidate:
    source: SessionInputVideo
    session_deleted: bool


class RetentionService:
    def __init__(
        self,
        store: JobStore,
        settings: RetentionSettings | None = None,
        *,
        session_media: SessionMediaStore | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.store = store
        self.database = store.database
        self.settings = settings or RetentionSettings.from_env()
        self.session_media = session_media
        self._now = now or (lambda: datetime.now(UTC))

    @asynccontextmanager
    async def cleanup_lock(self) -> AsyncIterator[bool]:
        connection = None
        acquired = False
        try:
            connection = await self.database.engine.connect()
            acquired = bool(
                await connection.scalar(
                    text("SELECT pg_try_advisory_lock(:key)"),
                    {"key": CLEANUP_ADVISORY_LOCK},
                )
            )
            yield acquired
        except SQLAlchemyError:
            raise JobStoreError(
                "DATABASE_UNAVAILABLE",
                "retention coordination is unavailable",
            ) from None
        finally:
            if connection is not None:
                try:
                    if acquired and not connection.closed:
                        await connection.execute(
                            text("SELECT pg_advisory_unlock(:key)"),
                            {"key": CLEANUP_ADVISORY_LOCK},
                        )
                except SQLAlchemyError:
                    pass
                await connection.close()

    async def preview(self, *, limit: int | None = None) -> dict[str, Any]:
        batch_limit = self._limit(limit)
        now = self._now()
        media = await self._media_candidates(now, batch_limit)
        sources = await self._source_candidates(now, batch_limit)
        audit = await self._audit_candidates(now, batch_limit)
        media_items = await self._media_preview_items(media, now)
        return {
            "ok": True,
            "mode": "preview",
            "as_of": _iso(now),
            "limit": batch_limit,
            "media": {
                "selected": len(media_items),
                "estimated_bytes": sum(item["estimated_bytes"] for item in media_items),
                "items": media_items,
            },
            "session_sources": {
                "selected": len(sources),
                "estimated_bytes": sum(
                    int(candidate.source.received_bytes) for candidate in sources
                ),
                "items": [
                    self._source_preview_item(candidate, now) for candidate in sources
                ],
            },
            "audit": {
                "selected": len(audit),
                "items": [
                    {
                        "job_id": row.id,
                        "editing_session_id": row.editing_session_id,
                        "audit_expires_at": _iso(row.audit_expires_at),
                    }
                    for row in audit
                ],
            },
        }

    async def status(self) -> dict[str, Any]:
        now = self._now()
        try:
            async with self.database.sessions() as session:
                due_media = await session.scalar(
                    select(func.count())
                    .select_from(VideoJob)
                    .join(EditingSession, EditingSession.id == VideoJob.editing_session_id)
                    .where(*self._media_conditions(now))
                )
                due_audit = await session.scalar(
                    select(func.count())
                    .select_from(VideoJob)
                    .join(EditingSession, EditingSession.id == VideoJob.editing_session_id)
                    .where(*self._audit_conditions(now))
                )
                due_sources = await session.scalar(
                    select(func.count())
                    .select_from(SessionInputVideo)
                    .join(
                        EditingSession,
                        EditingSession.id == SessionInputVideo.editing_session_id,
                    )
                    .where(*self._source_conditions(now))
                )
                held_sessions = await session.scalar(
                    select(func.count())
                    .select_from(EditingSession)
                    .where(EditingSession.audit_hold_at.is_not(None))
                )
        except SQLAlchemyError:
            raise JobStoreError("DATABASE_UNAVAILABLE", "retention status is unavailable") from None
        return {
            "ok": True,
            "enabled": self.settings.enabled,
            "media_days": self.settings.media_days,
            "audit_days": self.settings.audit_days,
            "interval_seconds": self.settings.interval_seconds,
            "batch_size": self.settings.batch_size,
            "incomplete_upload_hours": self.settings.incomplete_upload_hours,
            "due_media_jobs": int(due_media or 0),
            "due_session_sources": int(due_sources or 0),
            "due_audit_jobs": int(due_audit or 0),
            "held_sessions": int(held_sessions or 0),
            "as_of": _iso(now),
        }

    async def run(self, *, limit: int | None = None) -> dict[str, Any]:
        batch_limit = self._limit(limit)
        async with self.cleanup_lock() as acquired:
            if not acquired:
                return {
                    "ok": False,
                    "mode": "apply",
                    "lock_acquired": False,
                    "code": "RETENTION_BUSY",
                }
            now = self._now()
            source_rows = await self._source_candidates(now, batch_limit)
            source_report = {
                "selected": len(source_rows),
                "purged": 0,
                "failed": 0,
                "deleted_files": 0,
                "missing_files": 0,
                "bytes": 0,
            }
            for row in source_rows:
                if self.session_media is None:
                    source_report["failed"] += 1
                    continue
                reason = self._source_reason(row, now)
                try:
                    result = await self.session_media.purge(
                        row.source.editing_session_id,
                        reason=reason,
                        now=now,
                    )
                except (JobStoreError, OSError) as exc:
                    source_report["failed"] += 1
                    emit_event(
                        "session_source_purge_failed",
                        outcome="error",
                        error_code=sanitize_text(
                            getattr(exc, "code", "RETENTION_OPERATION_FAILED"),
                            limit=120,
                        ),
                    )
                    continue
                source_report["purged"] += int(result.get("selected", 1))
                for key in ("deleted_files", "missing_files", "bytes"):
                    source_report[key] += int(result.get(key, 0))
                if int(result.get("selected", 1)):
                    emit_event(
                        "session_source_purged",
                        editing_session_id=row.source.editing_session_id,
                        outcome="ok",
                        reason=reason,
                        deleted_files=int(result.get("deleted_files", 0)),
                        missing_files=int(result.get("missing_files", 0)),
                        bytes=int(result.get("bytes", 0)),
                    )
            media_rows = await self._media_candidates(now, batch_limit)
            media_report = {
                "selected": len(media_rows),
                "purged": 0,
                "failed": 0,
                "deleted_files": 0,
                "missing_files": 0,
                "bytes": 0,
            }
            for row in media_rows:
                reason = "session_deleted" if row.deleted_at is not None else "media_expired"
                try:
                    result = await self._purge_media_job(row.id, reason)
                except (JobStoreError, OSError) as exc:
                    media_report["failed"] += 1
                    await self._record_failure(row.id, "media_purge_failed", exc)
                    continue
                media_report["purged"] += 1
                for key in ("deleted_files", "missing_files", "bytes"):
                    media_report[key] += int(result[key])

            audit_rows = await self._audit_candidates(now, batch_limit)
            audit_report = {
                "selected": len(audit_rows),
                "deleted": 0,
                "failed": 0,
                "deleted_files": 0,
                "bytes": 0,
                "empty_sessions_deleted": 0,
            }
            for row in audit_rows:
                try:
                    result = await self._hard_delete_job(row.id, now)
                except (JobStoreError, OSError) as exc:
                    audit_report["failed"] += 1
                    await self._record_failure(row.id, "audit_delete_failed", exc)
                    continue
                if result["deleted"]:
                    audit_report["deleted"] += 1
                    audit_report["deleted_files"] += int(result["deleted_files"])
                    audit_report["bytes"] += int(result["bytes"])
            audit_report["empty_sessions_deleted"] = await self._delete_empty_sessions(
                now,
                batch_limit,
            )
            return {
                "ok": True,
                "mode": "apply",
                "lock_acquired": True,
                "as_of": _iso(now),
                "limit": batch_limit,
                "session_sources": source_report,
                "media": media_report,
                "audit": audit_report,
            }

    async def cleanup_terminal_job(self, job_id: str) -> dict[str, Any]:
        state = await self.store.load_for_audit(job_id)
        if state.get("state") not in TERMINAL_STATES:
            return {"deleted_files": 0, "bytes": 0}
        try:
            async with self.database.sessions() as session:
                registered = set(
                    (
                        await session.execute(
                            select(Artifact.relative_path).where(Artifact.job_id == job_id)
                        )
                    ).scalars()
                )
        except SQLAlchemyError:
            raise JobStoreError("DATABASE_UNAVAILABLE", "temporary cleanup is unavailable") from None
        result = await asyncio.to_thread(
            self._cleanup_terminal_files,
            job_id,
            registered,
        )
        if result["deleted_files"]:
            await self.store.record_event(
                job_id,
                "temporary_files_purged",
                result,
            )
        return result

    async def delete_session(self, session_id: str) -> dict[str, Any]:
        if not JOB_ID_PATTERN.fullmatch(str(session_id or "")):
            raise JobStoreError("SESSION_ID_INVALID", "invalid session id")
        async with self.cleanup_lock() as acquired:
            if not acquired:
                raise JobStoreError("RETENTION_BUSY", "retention cleanup is already running")
            now = self._now()
            try:
                async with self.database.sessions() as session:
                    async with session.begin():
                        owner = await session.scalar(
                            select(EditingSession)
                            .where(EditingSession.id == session_id)
                            .with_for_update()
                        )
                        if owner is None:
                            raise JobStoreError("SESSION_NOT_FOUND", "session not found")
                        jobs = list(
                            (
                                await session.execute(
                                    select(VideoJob)
                                    .where(VideoJob.editing_session_id == session_id)
                                    .order_by(VideoJob.created_at, VideoJob.id)
                                    .with_for_update()
                                )
                            ).scalars()
                        )
                        if any(job.state in ACTIVE_STATES for job in jobs):
                            raise JobStoreError(
                                "SESSION_ACTIVE_JOBS",
                                "active jobs must finish before deleting the session",
                            )
                        first_delete = owner.deleted_at is None
                        if first_delete:
                            owner.deleted_at = now
                            owner.audit_expires_at = now + self.store.audit_retention
                            owner.updated_at = now
                            for job in jobs:
                                job.deleted_at = now
                                job.audit_expires_at = owner.audit_expires_at
                                if job.media_expires_at is not None:
                                    job.media_expires_at = now
                                await self.store._append_event(
                                    session,
                                    job,
                                    "editing_session_deleted",
                                    {"editing_session_id": session_id},
                                )
            except JobStoreError:
                raise
            except SQLAlchemyError:
                raise JobStoreError("DATABASE_UNAVAILABLE", "session deletion failed") from None

            media_report = {
                "selected": 0,
                "purged": 0,
                "failed": 0,
                "deleted_files": 0,
                "missing_files": 0,
                "bytes": 0,
            }
            for job in jobs:
                if job.media_expires_at is None:
                    continue
                media_report["selected"] += 1
                try:
                    result = await self._purge_media_job(job.id, "session_deleted")
                except (JobStoreError, OSError) as exc:
                    media_report["failed"] += 1
                    await self._record_failure(job.id, "session_media_purge_failed", exc)
                    continue
                media_report["purged"] += 1
                for key in ("deleted_files", "missing_files", "bytes"):
                    media_report[key] += int(result[key])
            source_report = {
                "selected": 0,
                "purged": 0,
                "failed": 0,
                "deleted_files": 0,
                "missing_files": 0,
                "bytes": 0,
            }
            if self.session_media is not None:
                source_report["selected"] = 1
                try:
                    result = await self.session_media.purge(
                        session_id,
                        reason="session_deleted",
                        now=now,
                    )
                except (JobStoreError, OSError):
                    source_report["failed"] = 1
                else:
                    source_report["selected"] = int(result.get("selected", 1))
                    source_report["purged"] = source_report["selected"]
                    for key in ("deleted_files", "missing_files", "bytes"):
                        source_report[key] = int(result.get(key, 0))
            return {
                "ok": media_report["failed"] == 0 and source_report["failed"] == 0,
                "id": session_id,
                "already_deleted": not first_delete,
                "deleted_at": _iso(owner.deleted_at),
                "audit_expires_at": _iso(owner.audit_expires_at),
                "media_purge": media_report,
                "source_purge": source_report,
            }

    async def set_audit_hold(self, session_id: str, reason: str) -> dict[str, Any]:
        clean_reason = sanitize_text(reason, limit=500).strip()
        if not clean_reason:
            raise JobStoreError("AUDIT_HOLD_INVALID", "audit hold reason is required")
        return await self._change_audit_hold(session_id, reason=clean_reason)

    async def clear_audit_hold(self, session_id: str) -> dict[str, Any]:
        return await self._change_audit_hold(session_id, reason=None)

    async def _change_audit_hold(
        self,
        session_id: str,
        *,
        reason: str | None,
    ) -> dict[str, Any]:
        if not JOB_ID_PATTERN.fullmatch(str(session_id or "")):
            raise JobStoreError("SESSION_ID_INVALID", "invalid session id")
        async with self.cleanup_lock() as acquired:
            if not acquired:
                raise JobStoreError("RETENTION_BUSY", "retention cleanup is already running")
            now = self._now()
            try:
                async with self.database.sessions() as session:
                    async with session.begin():
                        owner = await session.scalar(
                            select(EditingSession)
                            .where(EditingSession.id == session_id)
                            .with_for_update()
                        )
                        if owner is None:
                            raise JobStoreError("SESSION_NOT_FOUND", "session not found")
                        owner.audit_hold_at = now if reason is not None else None
                        owner.audit_hold_reason = reason
                        jobs = list(
                            (
                                await session.execute(
                                    select(VideoJob)
                                    .where(VideoJob.editing_session_id == session_id)
                                    .with_for_update()
                                )
                            ).scalars()
                        )
                        event_type = "audit_hold_set" if reason is not None else "audit_hold_cleared"
                        for job in jobs:
                            await self.store._append_event(
                                session,
                                job,
                                event_type,
                                {"editing_session_id": session_id},
                            )
            except JobStoreError:
                raise
            except SQLAlchemyError:
                raise JobStoreError("DATABASE_UNAVAILABLE", "audit hold update failed") from None
        return {
            "ok": True,
            "editing_session_id": session_id,
            "held": reason is not None,
            "audit_hold_at": _iso(owner.audit_hold_at),
            "audit_hold_reason": owner.audit_hold_reason,
        }

    def _limit(self, limit: int | None) -> int:
        value = self.settings.batch_size if limit is None else int(limit)
        if not 1 <= value <= 1000:
            raise JobStoreError("RETENTION_BATCH_INVALID", "limit must be between 1 and 1000")
        return value

    def _media_conditions(self, now: datetime) -> tuple[Any, ...]:
        return (
            VideoJob.state.in_(TERMINAL_STATES),
            VideoJob.media_expires_at.is_not(None),
            or_(
                VideoJob.media_expires_at <= now,
                EditingSession.deleted_at.is_not(None),
            ),
        )

    def _source_conditions(self, now: datetime) -> tuple[Any, ...]:
        incomplete_states = {"pending", "uploading", "validating", "failed"}
        return (
            SessionInputVideo.state.in_({*incomplete_states, "ready"}),
            SessionInputVideo.purged_at.is_(None),
            or_(
                EditingSession.deleted_at.is_not(None),
                and_(
                    SessionInputVideo.state.in_(incomplete_states),
                    SessionInputVideo.expires_at.is_not(None),
                    SessionInputVideo.expires_at <= now,
                ),
                and_(
                    SessionInputVideo.state == "ready",
                    SessionInputVideo.expires_at.is_not(None),
                    SessionInputVideo.expires_at <= now,
                ),
            ),
            ~exists(
                select(VideoJob.id).where(
                    VideoJob.editing_session_id
                    == SessionInputVideo.editing_session_id,
                    VideoJob.state.in_(ACTIVE_STATES),
                )
            ),
        )

    def _audit_conditions(self, now: datetime) -> tuple[Any, ...]:
        return (
            VideoJob.state.in_(TERMINAL_STATES),
            VideoJob.audit_expires_at <= now,
            EditingSession.audit_hold_at.is_(None),
        )

    async def _media_candidates(
        self,
        now: datetime,
        limit: int,
    ) -> list[VideoJob]:
        try:
            async with self.database.sessions() as session:
                return list(
                    (
                        await session.execute(
                            select(VideoJob)
                            .join(
                                EditingSession,
                                EditingSession.id == VideoJob.editing_session_id,
                            )
                            .where(*self._media_conditions(now))
                            .order_by(VideoJob.media_expires_at, VideoJob.id)
                            .limit(limit)
                        )
                    ).scalars()
                )
        except SQLAlchemyError:
            raise JobStoreError("DATABASE_UNAVAILABLE", "retention preview is unavailable") from None

    async def _source_candidates(
        self,
        now: datetime,
        limit: int,
    ) -> list[SourceRetentionCandidate]:
        try:
            async with self.database.sessions() as session:
                rows = (
                    await session.execute(
                        select(
                            SessionInputVideo,
                            EditingSession.deleted_at.is_not(None),
                        )
                        .join(
                            EditingSession,
                            EditingSession.id == SessionInputVideo.editing_session_id,
                        )
                        .where(*self._source_conditions(now))
                        .order_by(
                            func.coalesce(
                                SessionInputVideo.expires_at,
                                SessionInputVideo.updated_at,
                            ),
                            SessionInputVideo.id,
                        )
                        .limit(limit)
                    )
                ).all()
        except SQLAlchemyError:
            raise JobStoreError(
                "DATABASE_UNAVAILABLE", "source retention preview is unavailable"
            ) from None
        return [
            SourceRetentionCandidate(source=row, session_deleted=bool(deleted))
            for row, deleted in rows
        ]

    def _source_preview_item(
        self,
        candidate: SourceRetentionCandidate,
        now: datetime,
    ) -> dict[str, Any]:
        row = candidate.source
        return {
            "source_id": row.id,
            "editing_session_id": row.editing_session_id,
            "state": row.state,
            "reason": self._source_reason(candidate, now),
            "expires_at": _iso(row.expires_at),
            "estimated_bytes": max(0, int(row.received_bytes)),
            "overdue": bool(row.expires_at and row.expires_at <= now),
        }

    @staticmethod
    def _source_reason(
        candidate: SourceRetentionCandidate,
        now: datetime,
    ) -> str:
        row = candidate.source
        if candidate.session_deleted:
            return "session_deleted"
        if row.state == "ready":
            return "source_expired"
        return "incomplete_upload_expired"

    async def _audit_candidates(
        self,
        now: datetime,
        limit: int,
    ) -> list[VideoJob]:
        try:
            async with self.database.sessions() as session:
                return list(
                    (
                        await session.execute(
                            select(VideoJob)
                            .join(
                                EditingSession,
                                EditingSession.id == VideoJob.editing_session_id,
                            )
                            .where(*self._audit_conditions(now))
                            .order_by(VideoJob.audit_expires_at, VideoJob.id)
                            .limit(limit)
                        )
                    ).scalars()
                )
        except SQLAlchemyError:
            raise JobStoreError("DATABASE_UNAVAILABLE", "retention preview is unavailable") from None

    async def _media_preview_items(
        self,
        rows: list[VideoJob],
        now: datetime,
    ) -> list[dict[str, Any]]:
        ids = [row.id for row in rows]
        sizes: dict[str, int] = {}
        if ids:
            try:
                async with self.database.sessions() as session:
                    for job_id, size in (
                        await session.execute(
                            select(Artifact.job_id, func.coalesce(func.sum(Artifact.size), 0))
                            .where(
                                Artifact.job_id.in_(ids),
                                Artifact.availability == "available",
                                or_(
                                    Artifact.kind == "video",
                                    Artifact.relative_path.like("%.zip"),
                                ),
                            )
                            .group_by(Artifact.job_id)
                        )
                    ).all():
                        sizes[job_id] = int(size or 0)
            except SQLAlchemyError:
                raise JobStoreError("DATABASE_UNAVAILABLE", "retention preview is unavailable") from None
        items = []
        for row in rows:
            input_size = (
                int((row.input_data or {}).get("size") or 0)
                if isinstance(row.input_data, dict)
                else 0
            )
            items.append(
                {
                    "job_id": row.id,
                    "editing_session_id": row.editing_session_id,
                    "reason": "session_deleted" if row.deleted_at is not None else "media_expired",
                    "media_expires_at": _iso(row.media_expires_at),
                    "estimated_bytes": max(0, input_size) + sizes.get(row.id, 0),
                    "overdue": bool(row.media_expires_at and row.media_expires_at <= now),
                }
            )
        return items

    async def _purge_media_job(self, job_id: str, reason: str) -> dict[str, int]:
        try:
            async with self.database.sessions() as session:
                row = await session.get(VideoJob, job_id)
                artifacts = list(
                    (
                        await session.execute(
                            select(Artifact).where(Artifact.job_id == job_id)
                        )
                    ).scalars()
                )
        except SQLAlchemyError:
            raise JobStoreError("DATABASE_UNAVAILABLE", "media retention is unavailable") from None
        if row is None or row.state not in TERMINAL_STATES or row.media_expires_at is None:
            return {"deleted_files": 0, "missing_files": 0, "bytes": 0}
        file_result = await asyncio.to_thread(
            self._purge_media_files,
            job_id,
            artifacts,
        )
        now = self._now()
        try:
            async with self.database.sessions() as session:
                async with session.begin():
                    locked = await session.scalar(
                        select(VideoJob).where(VideoJob.id == job_id).with_for_update()
                    )
                    if locked is None or locked.state not in TERMINAL_STATES:
                        raise JobStoreError("JOB_NOT_FOUND", "job not found")
                    stored_artifacts = list(
                        (
                            await session.execute(
                                select(Artifact)
                                .where(Artifact.job_id == job_id)
                                .with_for_update()
                            )
                        ).scalars()
                    )
                    for artifact in stored_artifacts:
                        status = file_result["artifact_status"].get(artifact.id)
                        if status == "deleted":
                            artifact.availability = "deleted"
                            artifact.purge_reason = reason
                            artifact.purged_at = now
                        elif status == "missing":
                            artifact.availability = "missing"
                            artifact.purge_reason = "media_missing"
                            artifact.purged_at = now
                    locked.media_expires_at = None
                    await self.store._append_event(
                        session,
                        locked,
                        "media_purged",
                        {
                            "reason": reason,
                            "deleted_files": file_result["deleted_files"],
                            "missing_files": file_result["missing_files"],
                            "bytes": file_result["bytes"],
                        },
                    )
        except JobStoreError:
            raise
        except SQLAlchemyError:
            raise JobStoreError("DATABASE_UNAVAILABLE", "media retention is unavailable") from None
        try:
            state = await self.store.load(job_id)
        except JobStoreError:
            pass
        else:
            await self.store._snapshot(state)
        return {
            "deleted_files": int(file_result["deleted_files"]),
            "missing_files": int(file_result["missing_files"]),
            "bytes": int(file_result["bytes"]),
        }

    async def _hard_delete_job(self, job_id: str, now: datetime) -> dict[str, int | bool]:
        file_result = await asyncio.to_thread(self._remove_job_tree, job_id)
        try:
            async with self.database.sessions() as session:
                async with session.begin():
                    row = await session.scalar(
                        select(VideoJob).where(VideoJob.id == job_id).with_for_update()
                    )
                    if row is None:
                        return {"deleted": False, **file_result}
                    owner = await session.scalar(
                        select(EditingSession)
                        .where(EditingSession.id == row.editing_session_id)
                        .with_for_update()
                    )
                    if (
                        row.state not in TERMINAL_STATES
                        or row.audit_expires_at > now
                        or owner is None
                        or owner.audit_hold_at is not None
                    ):
                        raise JobStoreError("AUDIT_DELETE_NOT_DUE", "audit record is not due")
                    await session.delete(row)
        except JobStoreError:
            raise
        except SQLAlchemyError:
            raise JobStoreError("DATABASE_UNAVAILABLE", "audit deletion is unavailable") from None
        return {"deleted": True, **file_result}

    async def _delete_empty_sessions(self, now: datetime, limit: int) -> int:
        try:
            async with self.database.sessions() as session:
                async with session.begin():
                    rows = list(
                        (
                            await session.execute(
                                select(EditingSession)
                                .where(
                                    EditingSession.audit_expires_at <= now,
                                    EditingSession.audit_hold_at.is_(None),
                                    ~exists(
                                        select(VideoJob.id).where(
                                            VideoJob.editing_session_id == EditingSession.id
                                        )
                                    ),
                                )
                                .order_by(EditingSession.audit_expires_at, EditingSession.id)
                                .limit(limit)
                                .with_for_update(skip_locked=True)
                            )
                        ).scalars()
                    )
                    for row in rows:
                        await session.delete(row)
        except SQLAlchemyError:
            raise JobStoreError("DATABASE_UNAVAILABLE", "session retention is unavailable") from None
        return len(rows)

    async def _record_failure(self, job_id: str, event_type: str, exc: Exception) -> None:
        code = sanitize_text(getattr(exc, "code", "RETENTION_OPERATION_FAILED"), limit=120)
        emit_event(event_type, job_id=job_id, outcome="error", error_code=code)
        try:
            await self.store.record_event(job_id, event_type, {"code": code})
        except Exception:
            pass

    def _validated_job_dir(self, job_id: str) -> Path:
        if not JOB_ID_PATTERN.fullmatch(str(job_id or "")):
            raise JobStoreError("JOB_ID_INVALID", "invalid job id")
        root = self.store.root.resolve()
        path = root / job_id
        if path.is_symlink():
            return path
        resolved = path.resolve(strict=False)
        if root not in resolved.parents:
            raise JobStoreError("RETENTION_PATH_INVALID", "job path is invalid")
        return resolved

    def _validated_relative(self, job_dir: Path, relative_path: str) -> Path:
        relative = Path(str(relative_path or ""))
        if relative.is_absolute() or ".." in relative.parts:
            raise JobStoreError("RETENTION_PATH_INVALID", "artifact path is invalid")
        path = job_dir / relative
        current = job_dir
        for part in relative.parts[:-1]:
            current /= part
            if current.is_symlink():
                raise JobStoreError("RETENTION_PATH_INVALID", "artifact path is invalid")
        return path

    @staticmethod
    def _unlink(path: Path) -> tuple[str, int]:
        if not os.path.lexists(path):
            return "missing", 0
        size = path.lstat().st_size if not path.is_dir() else 0
        if path.is_dir() and not path.is_symlink():
            raise JobStoreError("RETENTION_PATH_INVALID", "expected a file path")
        path.unlink()
        return "deleted", max(0, int(size))

    def _clear_directory(self, directory: Path) -> dict[str, int]:
        result = {"deleted_files": 0, "missing_files": 0, "bytes": 0}
        if not os.path.lexists(directory):
            return result
        if directory.is_symlink():
            status, size = self._unlink(directory)
            result[f"{status}_files"] += 1
            result["bytes"] += size
            return result
        for entry in os.scandir(directory):
            path = Path(entry.path)
            if entry.is_dir(follow_symlinks=False):
                nested = self._clear_directory(path)
                for key in result:
                    result[key] += nested[key]
                path.rmdir()
            else:
                status, size = self._unlink(path)
                result[f"{status}_files"] += 1
                result["bytes"] += size
        return result

    def _delete_matching_media(
        self,
        directory: Path,
        excluded: set[Path],
    ) -> dict[str, int]:
        result = {"deleted_files": 0, "missing_files": 0, "bytes": 0}
        if not directory.is_dir() or directory.is_symlink():
            return result
        for entry in os.scandir(directory):
            path = Path(entry.path)
            if entry.is_dir(follow_symlinks=False):
                nested = self._delete_matching_media(path, excluded)
                for key in result:
                    result[key] += nested[key]
                try:
                    path.rmdir()
                except OSError:
                    pass
            elif path not in excluded and path.suffix.lower() in MEDIA_SUFFIXES:
                status, size = self._unlink(path)
                result[f"{status}_files"] += 1
                result["bytes"] += size
        return result

    def _purge_media_files(
        self,
        job_id: str,
        artifacts: list[Artifact],
    ) -> dict[str, Any]:
        job_dir = self._validated_job_dir(job_id)
        result: dict[str, Any] = {
            "deleted_files": 0,
            "missing_files": 0,
            "bytes": 0,
            "artifact_status": {},
        }
        if job_dir.is_symlink():
            status, size = self._unlink(job_dir)
            result[f"{status}_files"] += 1
            result["bytes"] += size
            for artifact in artifacts:
                if artifact.kind == "video" or Path(artifact.relative_path).suffix.lower() in MEDIA_SUFFIXES:
                    result["artifact_status"][artifact.id] = "missing"
            return result
        media_artifacts: list[tuple[Artifact, Path]] = []
        for artifact in artifacts:
            if (
                artifact.kind != "video"
                and Path(artifact.relative_path).suffix.lower() not in MEDIA_SUFFIXES
            ):
                continue
            media_artifacts.append(
                (artifact, self._validated_relative(job_dir, artifact.relative_path))
            )
        for directory_name in ("input", "work"):
            current = self._clear_directory(job_dir / directory_name)
            for key in ("deleted_files", "missing_files", "bytes"):
                result[key] += current[key]
        excluded: set[Path] = set()
        for artifact, path in media_artifacts:
            excluded.add(path)
            status, size = self._unlink(path)
            result["artifact_status"][artifact.id] = status
            result[f"{status}_files"] += 1
            result["bytes"] += size
        unregistered = self._delete_matching_media(job_dir / "output", excluded)
        for key in ("deleted_files", "missing_files", "bytes"):
            result[key] += unregistered[key]
        return result

    def _cleanup_terminal_files(
        self,
        job_id: str,
        registered: set[str],
    ) -> dict[str, int]:
        job_dir = self._validated_job_dir(job_id)
        result = {"deleted_files": 0, "bytes": 0}
        if job_dir.is_symlink():
            return result
        work = self._clear_directory(job_dir / "work")
        result["deleted_files"] += work["deleted_files"]
        result["bytes"] += work["bytes"]
        output = job_dir / "output"
        if output.is_dir() and not output.is_symlink():
            for entry in os.scandir(output):
                path = Path(entry.path)
                relative = path.relative_to(job_dir).as_posix()
                if relative in registered:
                    continue
                if entry.is_dir(follow_symlinks=False):
                    nested = self._clear_directory(path)
                    result["deleted_files"] += nested["deleted_files"]
                    result["bytes"] += nested["bytes"]
                    path.rmdir()
                else:
                    status, size = self._unlink(path)
                    if status == "deleted":
                        result["deleted_files"] += 1
                        result["bytes"] += size
        return result

    def _remove_job_tree(self, job_id: str) -> dict[str, int]:
        job_dir = self._validated_job_dir(job_id)
        result = {"deleted_files": 0, "bytes": 0}
        if not os.path.lexists(job_dir):
            return result
        if job_dir.is_symlink():
            _status, size = self._unlink(job_dir)
            return {"deleted_files": 1, "bytes": size}
        cleared = self._clear_directory(job_dir)
        job_dir.rmdir()
        return {
            "deleted_files": cleared["deleted_files"],
            "bytes": cleared["bytes"],
        }


class RetentionScheduler:
    def __init__(self, service: RetentionService) -> None:
        self.service = service
        self._stop = asyncio.Event()
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        if not self.service.settings.enabled or self._task is not None:
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._loop(), name="openstoryline-retention")

    async def stop(self) -> None:
        if self._task is None:
            return
        self._stop.set()
        await self._task
        self._task = None

    async def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                result = await self.service.run(limit=self.service.settings.batch_size)
                emit_event(
                    "retention_pass",
                    outcome="ok" if result.get("ok") else "busy",
                    media_purged=(result.get("media") or {}).get("purged", 0),
                    audit_deleted=(result.get("audit") or {}).get("deleted", 0),
                )
            except Exception as exc:
                emit_event(
                    "retention_pass",
                    outcome="error",
                    error_code=sanitize_text(
                        getattr(exc, "code", "RETENTION_OPERATION_FAILED"),
                        limit=120,
                    ),
                )
            try:
                await asyncio.wait_for(
                    self._stop.wait(),
                    timeout=self.service.settings.interval_seconds,
                )
            except TimeoutError:
                pass
