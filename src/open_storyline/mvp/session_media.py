from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, AsyncIterator, Callable
import asyncio
import hashlib
import json
import math
import os
import re
import subprocess
import uuid

from sqlalchemy import func, select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession

from open_storyline.mvp.database import Database
from open_storyline.mvp.jobs import ACTIVE_STATES, JOB_ID_PATTERN, JobStoreError, _iso
from open_storyline.mvp.models import EditingSession, SessionInputVideo, VideoJob


ALLOWED_VIDEO_MEDIA_TYPES = {
    ".avi": frozenset({"video/x-msvideo"}),
    ".m4v": frozenset({"video/mp4", "video/x-m4v"}),
    ".mkv": frozenset({"video/x-matroska"}),
    ".mov": frozenset({"video/quicktime"}),
    ".mp4": frozenset({"application/mp4", "video/mp4"}),
    ".webm": frozenset({"video/webm"}),
}
CANONICAL_VIDEO_MEDIA_TYPES = {
    suffix: sorted(media_types)[-1]
    for suffix, media_types in ALLOWED_VIDEO_MEDIA_TYPES.items()
}
DEFAULT_UPLOAD_CHUNK_BYTES = 8 * 1024 * 1024
MAX_UPLOAD_CHUNK_BYTES = 64 * 1024 * 1024
MAX_PROBE_OUTPUT_BYTES = 1024 * 1024
SESSION_MEDIA_LOCK_SEED = 7_303_110_792_765


class SessionMediaError(JobStoreError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.details = details
        super().__init__(code, message)


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _bounded_env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)).strip())
    except ValueError:
        raise SessionMediaError(
            "SESSION_MEDIA_CONFIG_INVALID", f"{name} must be an integer"
        ) from None
    if not minimum <= value <= maximum:
        raise SessionMediaError(
            "SESSION_MEDIA_CONFIG_INVALID",
            f"{name} must be between {minimum} and {maximum}",
        )
    return value


def _safe_filename(value: str) -> tuple[str, str]:
    raw_name = Path(str(value or "").replace("\\", "/")).name
    suffix = Path(raw_name).suffix.lower()
    if suffix not in ALLOWED_VIDEO_MEDIA_TYPES:
        raise SessionMediaError(
            "VIDEO_TYPE_UNSUPPORTED",
            "the source video extension is unsupported",
        )
    stem = re.sub(r"[^a-zA-Z0-9._-]+", "-", Path(raw_name).stem).strip(".-")
    clean_stem = (stem or "source")[: max(1, 180 - len(suffix))]
    return f"{clean_stem}{suffix}", suffix


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _probe_video(path: Path, timeout_seconds: int) -> None:
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration:stream=codec_type,width,height",
                "-of",
                "json",
                str(path),
            ],
            capture_output=True,
            check=False,
            timeout=timeout_seconds,
        )
    except FileNotFoundError:
        raise SessionMediaError(
            "SOURCE_VALIDATION_UNAVAILABLE", "source validation is unavailable"
        ) from None
    except subprocess.TimeoutExpired:
        raise SessionMediaError(
            "SOURCE_VALIDATION_TIMEOUT", "source validation timed out"
        ) from None
    if (
        result.returncode != 0
        or len(result.stdout) > MAX_PROBE_OUTPUT_BYTES
        or len(result.stderr) > MAX_PROBE_OUTPUT_BYTES
    ):
        raise SessionMediaError("SOURCE_VIDEO_INVALID", "the source is not a valid video")
    try:
        payload = json.loads(result.stdout)
        video = next(
            stream
            for stream in payload.get("streams", [])
            if stream.get("codec_type") == "video"
        )
        duration = float((payload.get("format") or {}).get("duration"))
        width = int(video["width"])
        height = int(video["height"])
    except (KeyError, StopIteration, TypeError, ValueError, json.JSONDecodeError):
        raise SessionMediaError("SOURCE_VIDEO_INVALID", "the source is not a valid video") from None
    if not math.isfinite(duration) or duration <= 0 or width <= 0 or height <= 0:
        raise SessionMediaError("SOURCE_VIDEO_INVALID", "the source is not a valid video")


class SessionMediaStore:
    """Session-owned source bytes coordinated by PostgreSQL advisory locks."""

    def __init__(
        self,
        root: str | Path,
        database: Database,
        *,
        media_retention_days: int = 7,
        incomplete_upload_hours: int = 24,
        max_upload_bytes: int | None = None,
        max_chunk_bytes: int | None = None,
        probe_timeout_seconds: int = 30,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.database = database
        self.media_retention = timedelta(days=int(media_retention_days))
        self.incomplete_retention = timedelta(hours=int(incomplete_upload_hours))
        self.max_upload_bytes = max_upload_bytes or _bounded_env_int(
            "OPENSTORYLINE_MAX_UPLOAD_BYTES",
            8 * 1024 * 1024 * 1024,
            1,
            1024 * 1024 * 1024 * 1024,
        )
        self.max_chunk_bytes = int(max_chunk_bytes or DEFAULT_UPLOAD_CHUNK_BYTES)
        if not 64 * 1024 <= self.max_chunk_bytes <= MAX_UPLOAD_CHUNK_BYTES:
            raise SessionMediaError(
                "SESSION_MEDIA_CONFIG_INVALID",
                "session upload chunk size is invalid",
            )
        self.probe_timeout_seconds = max(1, min(int(probe_timeout_seconds), 120))
        self._now = now or _utcnow

    async def initialize(
        self,
        session_id: str,
        *,
        original_filename: str,
        expected_size: int,
        media_type: str | None,
    ) -> dict[str, Any]:
        clean_name, suffix = _safe_filename(original_filename)
        size = int(expected_size)
        if size <= 0:
            raise SessionMediaError("UPLOAD_SIZE_INVALID", "upload size must be positive")
        if size > self.max_upload_bytes:
            raise SessionMediaError(
                "UPLOAD_TOO_LARGE",
                "upload exceeds the configured limit",
                details={"max_upload_bytes": self.max_upload_bytes},
            )
        declared_type = str(media_type or "").split(";", 1)[0].strip().lower()
        if declared_type and declared_type not in ALLOWED_VIDEO_MEDIA_TYPES[suffix]:
            raise SessionMediaError(
                "VIDEO_TYPE_UNSUPPORTED", "the source video media type is unsupported"
            )
        canonical_type = CANONICAL_VIDEO_MEDIA_TYPES[suffix]

        async with self._session_lock(session_id) as connection:
            deferred_error: JobStoreError | None = None
            try:
                async with self._session(connection) as session:
                    async with session.begin():
                        owner = await self._active_owner(session, session_id, for_update=True)
                        row = await session.scalar(
                            select(SessionInputVideo)
                            .where(SessionInputVideo.editing_session_id == session_id)
                            .with_for_update()
                        )
                        if row is None:
                            self._ensure_input_dir(session_id)
                            now = self._now()
                            row = SessionInputVideo(
                                id=uuid.uuid4().hex,
                                editing_session_id=session_id,
                                state="uploading",
                                original_filename=clean_name,
                                expected_size=size,
                                received_bytes=0,
                                media_type=canonical_type,
                                created_at=now,
                                updated_at=now,
                                expires_at=now + self.incomplete_retention,
                            )
                            session.add(row)
                        else:
                            deferred_error = self._reconcile_locked(row)
                            if deferred_error is None:
                                if row.state in {"ready", "expired", "deleted"}:
                                    deferred_error = SessionMediaError(
                                        "SESSION_SOURCE_IMMUTABLE",
                                        "this session source cannot be replaced",
                                    )
                                elif row.state == "validating":
                                    if not self._metadata_matches(
                                        row, clean_name, size, canonical_type
                                    ):
                                        deferred_error = SessionMediaError(
                                            "UPLOAD_METADATA_CONFLICT",
                                            "upload metadata does not match the active upload",
                                            details={"upload_offset": int(row.received_bytes)},
                                        )
                                elif row.state == "uploading":
                                    if not self._metadata_matches(
                                        row, clean_name, size, canonical_type
                                    ):
                                        deferred_error = SessionMediaError(
                                            "UPLOAD_METADATA_CONFLICT",
                                            "upload metadata does not match the active upload",
                                            details={"upload_offset": int(row.received_bytes)},
                                        )
                                elif row.state in {"pending", "failed"}:
                                    self._remove_incomplete_files(row)
                                    now = self._now()
                                    row.state = "uploading"
                                    row.original_filename = clean_name
                                    row.expected_size = size
                                    row.received_bytes = 0
                                    row.media_type = canonical_type
                                    row.relative_path = None
                                    row.sha256 = None
                                    row.failure_code = None
                                    row.completed_at = None
                                    row.expires_at = now + self.incomplete_retention
                                    row.purged_at = None
                                    row.updated_at = now
                        if deferred_error is None:
                            now = self._now()
                            row.updated_at = now
                            row.expires_at = now + self.incomplete_retention
                            owner.updated_at = now
                        state = self._state(row)
            except JobStoreError:
                raise
            except SQLAlchemyError:
                raise SessionMediaError(
                    "DATABASE_UNAVAILABLE", "session source storage is unavailable"
                ) from None
            if deferred_error is not None:
                raise deferred_error
            return state

    async def status(self, session_id: str) -> dict[str, Any]:
        async with self._session_lock(session_id) as connection:
            try:
                async with self._session(connection) as session:
                    async with session.begin():
                        await self._active_owner(session, session_id)
                        row = await session.scalar(
                            select(SessionInputVideo)
                            .where(SessionInputVideo.editing_session_id == session_id)
                            .with_for_update()
                        )
                        if row is None:
                            return self._missing_state(session_id)
                        self._reconcile_locked(row)
                        return self._state(row)
            except JobStoreError:
                raise
            except SQLAlchemyError:
                raise SessionMediaError(
                    "DATABASE_UNAVAILABLE", "session source status is unavailable"
                ) from None

    async def append_chunk(
        self,
        session_id: str,
        upload_id: str,
        *,
        offset: int,
        chunks: AsyncIterator[bytes],
        content_length: int | None = None,
    ) -> dict[str, Any]:
        if int(offset) < 0:
            raise SessionMediaError("UPLOAD_OFFSET_INVALID", "upload offset is invalid")
        if content_length is not None and (
            int(content_length) <= 0 or int(content_length) > self.max_chunk_bytes
        ):
            raise SessionMediaError(
                "UPLOAD_CHUNK_INVALID",
                "upload chunk size is invalid",
                details={"max_chunk_bytes": self.max_chunk_bytes},
            )

        async with self._session_lock(session_id) as connection:
            row = await self._load_upload_for_write(
                session_id, upload_id, connection=connection
            )
            if row["state"] not in {"pending", "uploading"}:
                raise SessionMediaError(
                    "UPLOAD_STATE_INVALID", "the upload cannot accept more chunks"
                )
            if int(offset) != int(row["received_bytes"]):
                raise SessionMediaError(
                    "UPLOAD_OFFSET_MISMATCH",
                    "upload offset does not match the authoritative offset",
                    details={"upload_offset": int(row["received_bytes"])},
                )
            if (
                content_length is not None
                and int(offset) + int(content_length) > row["expected_size"]
            ):
                raise SessionMediaError(
                    "UPLOAD_CHUNK_INVALID", "upload chunk exceeds the expected source size"
                )

            part_path = self._part_path(session_id)
            written = await self._append_stream(
                part_path,
                offset=int(offset),
                expected_size=int(row["expected_size"]),
                chunks=chunks,
            )
            if written <= 0:
                raise SessionMediaError("UPLOAD_CHUNK_INVALID", "upload chunk is empty")

            try:
                async with self._session(connection) as session:
                    async with session.begin():
                        owner = await self._active_owner(session, session_id, for_update=True)
                        locked = await session.scalar(
                            select(SessionInputVideo)
                            .where(
                                SessionInputVideo.editing_session_id == session_id,
                                SessionInputVideo.id == upload_id,
                            )
                            .with_for_update()
                        )
                        if locked is None:
                            raise SessionMediaError("SOURCE_UPLOAD_NOT_FOUND", "upload not found")
                        deferred_error = self._reconcile_locked(locked)
                        now = self._now()
                        locked.state = "uploading" if deferred_error is None else locked.state
                        locked.updated_at = now
                        locked.expires_at = now + self.incomplete_retention
                        owner.updated_at = now
                        state = self._state(locked)
                if deferred_error is not None:
                    raise deferred_error
                return state
            except JobStoreError:
                raise
            except SQLAlchemyError:
                raise SessionMediaError(
                    "DATABASE_UNAVAILABLE", "upload progress could not be recorded"
                ) from None

    async def complete(self, session_id: str, upload_id: str) -> dict[str, Any]:
        async with self._session_lock(session_id) as connection:
            row = await self._load_upload_for_write(
                session_id, upload_id, connection=connection
            )
            if row["state"] == "ready":
                return row
            if row["state"] in {"expired", "deleted"}:
                raise SessionMediaError(
                    "SESSION_SOURCE_IMMUTABLE", "this session source cannot be replaced"
                )
            if row["state"] == "failed":
                raise SessionMediaError("SOURCE_UPLOAD_FAILED", "the upload must be restarted")
            if row["state"] not in {"uploading", "validating"}:
                raise SessionMediaError("UPLOAD_STATE_INVALID", "the upload cannot be completed")
            if row["received_bytes"] != row["expected_size"]:
                raise SessionMediaError(
                    "UPLOAD_INCOMPLETE",
                    "the upload has not received all expected bytes",
                    details={"upload_offset": int(row["received_bytes"])},
                )

            suffix = Path(row["original_filename"]).suffix.lower()
            part_path = self._part_path(session_id)
            final_path = self._final_path(session_id, suffix)
            candidate = final_path if final_path.is_file() else part_path
            try:
                candidate = self._validated_existing_file(candidate)
                if candidate.stat().st_size != row["expected_size"]:
                    raise SessionMediaError(
                        "UPLOAD_STORAGE_DIVERGED", "uploaded bytes do not match metadata"
                    )
            except SessionMediaError as exc:
                await self._fail_validation(
                    session_id, upload_id, exc.code, connection=connection
                )
                raise

            await self._set_validating(
                session_id, upload_id, connection=connection
            )
            try:
                await asyncio.to_thread(
                    _probe_video, candidate, self.probe_timeout_seconds
                )
                digest = await asyncio.to_thread(_hash_file, candidate)
                if candidate != final_path:
                    if os.path.lexists(final_path):
                        raise SessionMediaError(
                            "SESSION_SOURCE_IMMUTABLE", "the source destination already exists"
                        )
                    await asyncio.to_thread(os.replace, candidate, final_path)
            except SessionMediaError as exc:
                await self._fail_validation(
                    session_id, upload_id, exc.code, connection=connection
                )
                raise
            except OSError:
                await self._fail_validation(
                    session_id,
                    upload_id,
                    "SOURCE_VALIDATION_STORAGE_FAILED",
                    connection=connection,
                )
                raise SessionMediaError(
                    "SOURCE_VALIDATION_STORAGE_FAILED", "source validation storage failed"
                ) from None

            now = self._now()
            try:
                async with self._session(connection) as session:
                    async with session.begin():
                        owner = await self._active_owner(session, session_id, for_update=True)
                        locked = await session.scalar(
                            select(SessionInputVideo)
                            .where(
                                SessionInputVideo.editing_session_id == session_id,
                                SessionInputVideo.id == upload_id,
                            )
                            .with_for_update()
                        )
                        if locked is None or locked.state != "validating":
                            raise SessionMediaError(
                                "UPLOAD_STATE_INVALID", "the upload cannot be completed"
                            )
                        locked.state = "ready"
                        locked.received_bytes = locked.expected_size
                        locked.media_type = CANONICAL_VIDEO_MEDIA_TYPES[suffix]
                        locked.relative_path = final_path.relative_to(self.root).as_posix()
                        locked.sha256 = digest
                        locked.failure_code = None
                        locked.completed_at = now
                        locked.expires_at = now + self.media_retention
                        locked.purged_at = None
                        locked.updated_at = now
                        owner.updated_at = now
                        state = self._state(locked)
            except JobStoreError:
                raise
            except SQLAlchemyError:
                raise SessionMediaError(
                    "DATABASE_UNAVAILABLE", "source completion could not be recorded"
                ) from None
            return state

    async def cancel(self, session_id: str, upload_id: str) -> dict[str, Any]:
        async with self._session_lock(session_id) as connection:
            row = await self._load_upload_for_write(
                session_id, upload_id, connection=connection
            )
            if row["state"] in {"ready", "expired", "deleted"}:
                raise SessionMediaError(
                    "SESSION_SOURCE_IMMUTABLE", "this session source cannot be cancelled"
                )
            result = await asyncio.to_thread(
                self._unlink_paths,
                [
                    self._part_path(session_id),
                    self._final_path(
                        session_id, Path(row["original_filename"]).suffix.lower()
                    ),
                ],
            )
            now = self._now()
            try:
                async with self._session(connection) as session:
                    async with session.begin():
                        owner = await self._active_owner(session, session_id, for_update=True)
                        locked = await session.scalar(
                            select(SessionInputVideo)
                            .where(
                                SessionInputVideo.editing_session_id == session_id,
                                SessionInputVideo.id == upload_id,
                            )
                            .with_for_update()
                        )
                        if locked is None:
                            raise SessionMediaError("SOURCE_UPLOAD_NOT_FOUND", "upload not found")
                        locked.state = "failed"
                        locked.received_bytes = 0
                        locked.relative_path = None
                        locked.sha256 = None
                        locked.failure_code = "UPLOAD_CANCELLED"
                        locked.completed_at = None
                        locked.expires_at = now + self.incomplete_retention
                        locked.purged_at = now
                        locked.updated_at = now
                        owner.updated_at = now
                        state = self._state(locked)
            except JobStoreError:
                raise
            except SQLAlchemyError:
                raise SessionMediaError(
                    "DATABASE_UNAVAILABLE", "upload cancellation could not be recorded"
                ) from None
            return {**state, "purge": result}

    async def resolve_ready(self, session_id: str) -> tuple[Path, dict[str, Any]]:
        async with self._session_lock(session_id) as connection:
            try:
                async with self._session(connection) as session:
                    await self._active_owner(session, session_id)
                    row = await session.scalar(
                        select(SessionInputVideo).where(
                            SessionInputVideo.editing_session_id == session_id
                        )
                    )
            except JobStoreError:
                raise
            except SQLAlchemyError:
                raise SessionMediaError(
                    "DATABASE_UNAVAILABLE", "source preview is unavailable"
                ) from None
            if row is None:
                raise SessionMediaError("SESSION_SOURCE_NOT_FOUND", "source not found")
            if row.state == "ready" and row.expires_at and row.expires_at <= self._now():
                raise SessionMediaError("SESSION_SOURCE_EXPIRED", "source media has expired")
            if row.state != "ready" or row.purged_at is not None:
                raise SessionMediaError("SESSION_SOURCE_UNAVAILABLE", "source is unavailable")
            try:
                path = self._resolve_relative(row.relative_path)
                path = self._validated_existing_file(path)
            except SessionMediaError:
                raise SessionMediaError(
                    "SESSION_SOURCE_UNAVAILABLE", "source is unavailable"
                ) from None
            return path, self._state(row)

    async def purge(
        self,
        session_id: str,
        *,
        reason: str,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        current = now or self._now()
        async with self._session_lock(session_id) as connection:
            try:
                async with self._session(connection) as session:
                    async with session.begin():
                        owner = await session.scalar(
                            select(EditingSession)
                            .where(EditingSession.id == session_id)
                            .with_for_update()
                        )
                        row = await session.scalar(
                            select(SessionInputVideo)
                            .where(SessionInputVideo.editing_session_id == session_id)
                            .with_for_update()
                        )
                        active_jobs = await session.scalar(
                            select(func.count())
                            .select_from(VideoJob)
                            .where(
                                VideoJob.editing_session_id == session_id,
                                VideoJob.state.in_(ACTIVE_STATES),
                            )
                        )
                        if owner is None or row is None:
                            return self._empty_purge(session_id)
                        if int(active_jobs or 0) > 0:
                            raise SessionMediaError(
                                "SESSION_ACTIVE_JOBS",
                                "active jobs prevent source media purge",
                            )
                        if reason != "session_deleted":
                            if row.expires_at is None or row.expires_at > current:
                                return self._empty_purge(session_id)
                        if row.purged_at is not None and row.state in {
                            "failed",
                            "expired",
                            "deleted",
                        }:
                            return self._empty_purge(session_id)
                        original_state = row.state
                        paths = self._purge_paths(row)
            except JobStoreError:
                raise
            except SQLAlchemyError:
                raise SessionMediaError(
                    "DATABASE_UNAVAILABLE", "source retention is unavailable"
                ) from None

            result = await asyncio.to_thread(self._unlink_paths, paths)
            try:
                async with self._session(connection) as session:
                    async with session.begin():
                        locked = await session.scalar(
                            select(SessionInputVideo)
                            .where(SessionInputVideo.editing_session_id == session_id)
                            .with_for_update()
                        )
                        if locked is None:
                            return self._empty_purge(session_id)
                        if reason == "session_deleted":
                            locked.state = "deleted"
                            locked.failure_code = "SESSION_DELETED"
                        elif original_state == "ready":
                            locked.state = "expired"
                            locked.failure_code = "SOURCE_EXPIRED"
                        else:
                            locked.state = "failed"
                            locked.failure_code = "UPLOAD_EXPIRED"
                        locked.purged_at = current
                        locked.updated_at = current
                        state = self._state(locked)
            except SQLAlchemyError:
                raise SessionMediaError(
                    "DATABASE_UNAVAILABLE", "source retention could not be recorded"
                ) from None
            return {**result, "selected": 1, "source": state, "reason": reason}

    async def renew_ready_expiry(self, session_id: str, *, now: datetime | None = None) -> None:
        current = now or self._now()
        try:
            async with self.database.sessions() as session:
                async with session.begin():
                    row = await session.scalar(
                        select(SessionInputVideo)
                        .where(
                            SessionInputVideo.editing_session_id == session_id,
                            SessionInputVideo.state == "ready",
                        )
                        .with_for_update()
                    )
                    if row is not None:
                        row.expires_at = current + self.media_retention
                        row.updated_at = current
        except SQLAlchemyError:
            raise SessionMediaError(
                "DATABASE_UNAVAILABLE", "source retention could not be renewed"
            ) from None

    async def _load_upload_for_write(
        self,
        session_id: str,
        upload_id: str,
        *,
        connection: AsyncConnection | None = None,
    ) -> dict[str, Any]:
        if not JOB_ID_PATTERN.fullmatch(str(upload_id or "")):
            raise SessionMediaError("SOURCE_UPLOAD_NOT_FOUND", "upload not found")
        try:
            async with self._session(connection) as session:
                async with session.begin():
                    await self._active_owner(session, session_id)
                    row = await session.scalar(
                        select(SessionInputVideo)
                        .where(
                            SessionInputVideo.editing_session_id == session_id,
                            SessionInputVideo.id == upload_id,
                        )
                        .with_for_update()
                    )
                    if row is None:
                        raise SessionMediaError("SOURCE_UPLOAD_NOT_FOUND", "upload not found")
                    deferred_error = self._reconcile_locked(row)
                    state = self._state(row)
            if deferred_error is not None:
                raise deferred_error
            return state
        except JobStoreError:
            raise
        except SQLAlchemyError:
            raise SessionMediaError(
                "DATABASE_UNAVAILABLE", "source upload is unavailable"
            ) from None

    async def _set_validating(
        self,
        session_id: str,
        upload_id: str,
        *,
        connection: AsyncConnection | None = None,
    ) -> None:
        try:
            async with self._session(connection) as session:
                async with session.begin():
                    row = await session.scalar(
                        select(SessionInputVideo)
                        .where(
                            SessionInputVideo.editing_session_id == session_id,
                            SessionInputVideo.id == upload_id,
                        )
                        .with_for_update()
                    )
                    if row is None or row.state not in {"uploading", "validating"}:
                        raise SessionMediaError(
                            "UPLOAD_STATE_INVALID", "the upload cannot be validated"
                        )
                    row.state = "validating"
                    row.updated_at = self._now()
        except JobStoreError:
            raise
        except SQLAlchemyError:
            raise SessionMediaError(
                "DATABASE_UNAVAILABLE", "source validation could not start"
            ) from None

    async def _fail_validation(
        self,
        session_id: str,
        upload_id: str,
        code: str,
        *,
        connection: AsyncConnection | None = None,
    ) -> None:
        paths = [self._part_path(session_id)]
        try:
            clean_name = await self._source_filename(
                session_id, upload_id, connection=connection
            )
        except JobStoreError:
            clean_name = None
        if clean_name:
            paths.append(self._final_path(session_id, Path(clean_name).suffix.lower()))
        await asyncio.to_thread(self._unlink_paths, paths)
        now = self._now()
        try:
            async with self._session(connection) as session:
                async with session.begin():
                    row = await session.scalar(
                        select(SessionInputVideo)
                        .where(
                            SessionInputVideo.editing_session_id == session_id,
                            SessionInputVideo.id == upload_id,
                        )
                        .with_for_update()
                    )
                    if row is not None and row.state != "ready":
                        row.state = "failed"
                        row.received_bytes = 0
                        row.relative_path = None
                        row.sha256 = None
                        row.failure_code = str(code)[:80]
                        row.completed_at = None
                        row.purged_at = now
                        row.updated_at = now
        except SQLAlchemyError:
            return

    async def _source_filename(
        self,
        session_id: str,
        upload_id: str,
        *,
        connection: AsyncConnection | None = None,
    ) -> str | None:
        try:
            async with self._session(connection) as session:
                return await session.scalar(
                    select(SessionInputVideo.original_filename).where(
                        SessionInputVideo.editing_session_id == session_id,
                        SessionInputVideo.id == upload_id,
                    )
                )
        except SQLAlchemyError:
            raise SessionMediaError(
                "DATABASE_UNAVAILABLE", "source validation is unavailable"
            ) from None

    async def _active_owner(self, session, session_id: str, *, for_update: bool = False):
        if not JOB_ID_PATTERN.fullmatch(str(session_id or "")):
            raise SessionMediaError("SESSION_NOT_FOUND", "session not found")
        query = select(EditingSession).where(EditingSession.id == session_id)
        if for_update:
            query = query.with_for_update()
        owner = await session.scalar(query)
        if (
            owner is None
            or owner.deleted_at is not None
            or owner.audit_expires_at <= self._now()
        ):
            raise SessionMediaError("SESSION_NOT_FOUND", "session not found")
        if owner.workflow_version != 2:
            raise SessionMediaError(
                "SESSION_WORKFLOW_LEGACY",
                "create a new reusable session before uploading a session source",
            )
        return owner

    @asynccontextmanager
    async def _session_lock(self, session_id: str) -> AsyncIterator[AsyncConnection]:
        if not JOB_ID_PATTERN.fullmatch(str(session_id or "")):
            raise SessionMediaError("SESSION_NOT_FOUND", "session not found")
        connection = None
        acquired = False
        try:
            connection = await self.database.engine.connect()
            acquired = bool(
                await connection.scalar(
                    text(
                        "SELECT pg_try_advisory_lock("
                        "hashtextextended(:lock_name, :seed))"
                    ),
                    {
                        "lock_name": f"openstoryline:session-source:{session_id}",
                        "seed": SESSION_MEDIA_LOCK_SEED,
                    },
                )
            )
            await connection.commit()
            if not acquired:
                raise SessionMediaError("SOURCE_UPLOAD_BUSY", "source upload is busy")
            yield connection
        except JobStoreError:
            raise
        except SQLAlchemyError:
            raise SessionMediaError(
                "DATABASE_UNAVAILABLE", "source upload coordination is unavailable"
            ) from None
        finally:
            if connection is not None:
                try:
                    if acquired and not connection.closed:
                        await connection.execute(
                            text(
                                "SELECT pg_advisory_unlock("
                                "hashtextextended(:lock_name, :seed))"
                            ),
                            {
                                "lock_name": f"openstoryline:session-source:{session_id}",
                                "seed": SESSION_MEDIA_LOCK_SEED,
                            },
                        )
                        await connection.commit()
                except SQLAlchemyError:
                    pass
                await connection.close()

    def _session(self, connection: AsyncConnection | None = None) -> AsyncSession:
        if connection is None:
            return self.database.sessions()
        return AsyncSession(bind=connection, expire_on_commit=False)

    async def _append_stream(
        self,
        path: Path,
        *,
        offset: int,
        expected_size: int,
        chunks: AsyncIterator[bytes],
    ) -> int:
        self._ensure_input_dir(path.parents[1].name)
        if path.is_symlink():
            raise SessionMediaError("UPLOAD_PATH_INVALID", "upload path is invalid")
        mode = "r+b" if path.exists() else "xb"
        stream = None
        written = 0
        rejected = False
        try:
            stream = await asyncio.to_thread(path.open, mode)
            await asyncio.to_thread(stream.seek, offset)
            async for chunk in chunks:
                if not chunk:
                    continue
                if (
                    written + len(chunk) > self.max_chunk_bytes
                    or offset + written + len(chunk) > expected_size
                ):
                    rejected = True
                    break
                await asyncio.to_thread(stream.write, chunk)
                written += len(chunk)
            if rejected:
                await asyncio.to_thread(stream.truncate, offset)
                raise SessionMediaError(
                    "UPLOAD_CHUNK_INVALID", "upload chunk exceeds the allowed size"
                )
            await asyncio.to_thread(stream.flush)
            await asyncio.to_thread(os.fsync, stream.fileno())
            return written
        except SessionMediaError:
            raise
        except OSError:
            raise SessionMediaError(
                "UPLOAD_WRITE_FAILED", "upload bytes could not be stored"
            ) from None
        finally:
            if stream is not None:
                try:
                    await asyncio.to_thread(stream.flush)
                    await asyncio.to_thread(os.fsync, stream.fileno())
                except OSError:
                    pass
                await asyncio.to_thread(stream.close)

    def _reconcile_locked(self, row: SessionInputVideo) -> JobStoreError | None:
        if row.state in {"ready", "expired", "deleted"}:
            return None
        if row.state == "failed" and row.purged_at is not None:
            return None
        part = self._part_path(row.editing_session_id)
        final = self._final_path(
            row.editing_session_id, Path(row.original_filename).suffix.lower()
        )
        if part.is_symlink() or final.is_symlink():
            row.state = "failed"
            row.failure_code = "UPLOAD_PATH_INVALID"
            row.updated_at = self._now()
            return SessionMediaError("UPLOAD_PATH_INVALID", "upload path is invalid")
        existing = [path for path in (part, final) if path.exists()]
        if len(existing) > 1 or (final.exists() and row.state != "validating"):
            self._unlink_paths(existing)
            row.state = "failed"
            row.received_bytes = 0
            row.failure_code = "UPLOAD_STORAGE_DIVERGED"
            row.purged_at = self._now()
            row.updated_at = self._now()
            return SessionMediaError(
                "UPLOAD_STORAGE_DIVERGED", "uploaded bytes do not match metadata"
            )
        actual = existing[0].stat().st_size if existing else 0
        if actual > row.expected_size:
            self._unlink_paths(existing)
            row.state = "failed"
            row.received_bytes = 0
            row.failure_code = "UPLOAD_STORAGE_DIVERGED"
            row.purged_at = self._now()
            row.updated_at = self._now()
            return SessionMediaError(
                "UPLOAD_STORAGE_DIVERGED", "uploaded bytes do not match metadata"
            )
        if actual < row.received_bytes:
            row.state = "failed"
            row.received_bytes = actual
            row.failure_code = "UPLOAD_STORAGE_DIVERGED"
            row.updated_at = self._now()
            return SessionMediaError(
                "UPLOAD_STORAGE_DIVERGED", "uploaded bytes do not match metadata"
            )
        if actual != row.received_bytes:
            row.received_bytes = actual
            now = self._now()
            row.updated_at = now
            row.expires_at = now + self.incomplete_retention
        if row.state == "pending" and actual:
            row.state = "uploading"
        return None

    def _ensure_input_dir(self, session_id: str) -> Path:
        if not JOB_ID_PATTERN.fullmatch(str(session_id or "")):
            raise SessionMediaError("UPLOAD_PATH_INVALID", "upload path is invalid")
        session_dir = self.root / session_id
        input_dir = session_dir / "input"
        for path in (session_dir, input_dir):
            if os.path.lexists(path) and path.is_symlink():
                raise SessionMediaError("UPLOAD_PATH_INVALID", "upload path is invalid")
            path.mkdir(exist_ok=True)
        return input_dir

    def _part_path(self, session_id: str) -> Path:
        return self.root / session_id / "input" / "source.part"

    def _final_path(self, session_id: str, suffix: str) -> Path:
        if suffix not in ALLOWED_VIDEO_MEDIA_TYPES:
            raise SessionMediaError("VIDEO_TYPE_UNSUPPORTED", "video type is unsupported")
        return self.root / session_id / "input" / f"source{suffix}"

    def _resolve_relative(self, value: str | None) -> Path:
        relative = Path(str(value or ""))
        if not relative.parts or relative.is_absolute() or ".." in relative.parts:
            raise SessionMediaError("UPLOAD_PATH_INVALID", "source path is invalid")
        path = self.root / relative
        current = self.root
        for part in relative.parts[:-1]:
            current /= part
            if current.is_symlink():
                raise SessionMediaError("UPLOAD_PATH_INVALID", "source path is invalid")
        resolved = path.resolve(strict=False)
        if self.root not in resolved.parents:
            raise SessionMediaError("UPLOAD_PATH_INVALID", "source path is invalid")
        return path

    def _validated_existing_file(self, path: Path) -> Path:
        resolved = path.resolve(strict=False)
        if self.root not in resolved.parents or path.is_symlink() or not path.is_file():
            raise SessionMediaError("UPLOAD_PATH_INVALID", "source path is invalid")
        return path

    def _remove_incomplete_files(self, row: SessionInputVideo) -> dict[str, int]:
        return self._unlink_paths(self._purge_paths(row))

    def _purge_paths(self, row: SessionInputVideo) -> list[Path]:
        paths = [self._part_path(row.editing_session_id)]
        suffix = Path(row.original_filename).suffix.lower()
        if suffix in ALLOWED_VIDEO_MEDIA_TYPES:
            paths.append(self._final_path(row.editing_session_id, suffix))
        if row.relative_path:
            paths.append(self._resolve_relative(row.relative_path))
        return list(dict.fromkeys(paths))

    def _unlink_paths(self, paths: list[Path]) -> dict[str, int]:
        result = {"deleted_files": 0, "missing_files": 0, "bytes": 0}
        for path in paths:
            resolved = path.resolve(strict=False)
            if self.root not in resolved.parents or path.is_symlink():
                raise SessionMediaError("UPLOAD_PATH_INVALID", "source path is invalid")
            if not os.path.lexists(path):
                result["missing_files"] += 1
                continue
            if path.is_dir():
                raise SessionMediaError("UPLOAD_PATH_INVALID", "source path is invalid")
            size = path.stat().st_size
            path.unlink()
            result["deleted_files"] += 1
            result["bytes"] += max(0, int(size))
        return result

    @staticmethod
    def _metadata_matches(
        row: SessionInputVideo,
        filename: str,
        expected_size: int,
        media_type: str,
    ) -> bool:
        return (
            row.original_filename == filename
            and row.expected_size == expected_size
            and row.media_type == media_type
        )

    @staticmethod
    def _state(row: SessionInputVideo) -> dict[str, Any]:
        return {
            "id": row.id,
            "upload_id": row.id,
            "editing_session_id": row.editing_session_id,
            "state": row.state,
            "original_filename": row.original_filename,
            "expected_size": int(row.expected_size),
            "received_bytes": int(row.received_bytes),
            "upload_offset": int(row.received_bytes),
            "media_type": row.media_type,
            "sha256": row.sha256,
            "failure_code": row.failure_code,
            "completed_at": _iso(row.completed_at),
            "expires_at": _iso(row.expires_at),
            "purged_at": _iso(row.purged_at),
        }

    @staticmethod
    def _missing_state(session_id: str) -> dict[str, Any]:
        return {
            "id": None,
            "upload_id": None,
            "editing_session_id": session_id,
            "state": "missing",
            "received_bytes": 0,
            "upload_offset": 0,
        }

    @staticmethod
    def _empty_purge(session_id: str) -> dict[str, Any]:
        return {
            "editing_session_id": session_id,
            "selected": 0,
            "deleted_files": 0,
            "missing_files": 0,
            "bytes": 0,
        }
