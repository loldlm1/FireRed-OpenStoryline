from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, AsyncIterator
import asyncio
import hashlib
import shutil
import uuid

from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession

from open_storyline.mvp.edit_plan import (
    EditPlanError,
    validate_generated_asset_limit,
    validate_job_controls,
    validate_stock_asset_limit,
    validate_stock_asset_kind,
    validate_stock_policy,
)
from open_storyline.mvp.jobs import (
    ACTIVE_STATES,
    CAPACITY_ADVISORY_LOCK,
    JOB_ID_PATTERN,
    JobStore,
    JobStoreError,
    _decode_cursor,
    _encode_cursor,
    _iso,
)
from open_storyline.mvp.models import (
    Artifact,
    AuditDocument,
    EditingSession,
    PromptVersion,
    SessionInputVideo,
    VideoJob,
)
from open_storyline.mvp.observability import compact_prior_attempt_quality_feedback
from open_storyline.mvp.outcomes import outcome_summary
from open_storyline.mvp.session_media import SessionMediaStore


RECENT_ATTEMPTS_PER_VERSION = 3
DETAIL_ATTEMPT_LIMIT = 50
RERUN_UNAVAILABLE_CODES = frozenset({
    "SESSION_NOT_FOUND",
    "SESSION_SOURCE_EXPIRED",
    "SESSION_SOURCE_NOT_FOUND",
    "SESSION_SOURCE_UNAVAILABLE",
})


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def validate_run_settings(
    *,
    max_clips: int = 8,
    edit_mode: str = "legacy",
    asset_policy: str = "auto",
    max_generated_assets_per_clip: int = 2,
    stock_policy: str = "off",
    max_stock_assets_per_clip: int = 0,
    stock_asset_kind: str = "video",
) -> dict[str, Any]:
    if not 1 <= int(max_clips) <= 50:
        raise JobStoreError("MAX_CLIPS_INVALID", "max_clips must be between 1 and 50")
    try:
        normalized_edit_mode, normalized_asset_policy = validate_job_controls(
            edit_mode,
            asset_policy,
        )
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
    return {
        "settings_version": 2,
        "max_clips": int(max_clips),
        "edit_mode": normalized_edit_mode,
        "asset_policy": normalized_asset_policy,
        "max_generated_assets_per_clip": generated_asset_limit,
        "stock_policy": normalized_stock_policy,
        "max_stock_assets_per_clip": stock_asset_limit,
        "stock_asset_kind": normalized_stock_kind,
    }


class PromptVersionService:
    def __init__(
        self,
        store: JobStore,
        session_media: SessionMediaStore,
    ) -> None:
        self.store = store
        self.database = store.database
        self.session_media = session_media

    async def create_version(
        self,
        session_id: str,
        *,
        prompt: str,
        settings: dict[str, Any],
        prompt_version_id: str | None = None,
        job_id: str | None = None,
    ) -> dict[str, Any]:
        clean_prompt = str(prompt or "").strip()
        if not clean_prompt:
            raise JobStoreError("PROMPT_REQUIRED", "an editing prompt is required")
        if len(clean_prompt) > 12000:
            raise JobStoreError("PROMPT_INVALID", "the editing prompt is too long")
        settings = validate_run_settings(
            max_clips=settings.get("max_clips", 8),
            edit_mode=settings.get("edit_mode", "legacy"),
            asset_policy=settings.get("asset_policy", "auto"),
            max_generated_assets_per_clip=settings.get(
                "max_generated_assets_per_clip", 2
            ),
            stock_policy=settings.get("stock_policy", "off"),
            max_stock_assets_per_clip=settings.get("max_stock_assets_per_clip", 0),
            stock_asset_kind=settings.get("stock_asset_kind", "video"),
        )
        version_identifier = prompt_version_id or uuid.uuid4().hex
        job_identifier = job_id or uuid.uuid4().hex
        self._validate_identifier(version_identifier, "PROMPT_VERSION_ID_INVALID")
        self._validate_identifier(job_identifier, "JOB_ID_INVALID")
        job_dir = self.store._prepare_job_directories(
            job_identifier,
            include_input=False,
        )
        try:
            source_path, _source_state = await self._ready_source(session_id)
            source_hash = await asyncio.to_thread(_hash_file, source_path)
            async with self._coordination(session_id) as connection:
                async with self._session(connection) as session:
                    async with session.begin():
                        await self._capacity_lock(session)
                        owner, source = await self._locked_workspace(
                            session,
                            session_id,
                            source_hash=source_hash,
                        )
                        version_number = int(
                            await session.scalar(
                                select(
                                    func.coalesce(func.max(PromptVersion.version_number), 0)
                                ).where(PromptVersion.editing_session_id == session_id)
                            )
                            or 0
                        ) + 1
                        now = _utcnow()
                        version = PromptVersion(
                            id=version_identifier,
                            editing_session_id=session_id,
                            version_number=version_number,
                            prompt=clean_prompt,
                            settings_data=dict(settings),
                            created_at=now,
                        )
                        job = self._new_job(
                            job_id=job_identifier,
                            owner=owner,
                            source=source,
                            version=version,
                            attempt_number=1,
                            now=now,
                        )
                        session.add_all((version, job))
                        await session.flush()
                        await self.store._append_event(
                            session,
                            job,
                            "prompt_run_created",
                            {
                                "prompt_version_id": version.id,
                                "prompt_version_number": version.version_number,
                                "attempt_number": 1,
                            },
                        )
        except JobStoreError:
            shutil.rmtree(job_dir, ignore_errors=True)
            raise
        except IntegrityError:
            shutil.rmtree(job_dir, ignore_errors=True)
            raise JobStoreError(
                "PROMPT_VERSION_CONFLICT", "prompt version creation conflicted"
            ) from None
        except SQLAlchemyError:
            shutil.rmtree(job_dir, ignore_errors=True)
            raise JobStoreError(
                "DATABASE_UNAVAILABLE", "prompt version storage is unavailable"
            ) from None
        run = await self.store.load(job_identifier)
        await self.store._snapshot(run)
        return {
            "prompt_version": self._version_state(version, attempts=[run]),
            "run": run,
        }

    async def rerun(
        self,
        prompt_version_id: str,
        *,
        job_id: str | None = None,
        prior_attempt_id: str | None = None,
        use_quality_feedback: bool = False,
    ) -> dict[str, Any]:
        self._validate_identifier(prompt_version_id, "PROMPT_VERSION_NOT_FOUND")
        if use_quality_feedback and not prior_attempt_id:
            raise JobStoreError(
                "PRIOR_ATTEMPT_REQUIRED",
                "an explicit prior attempt is required when quality feedback is enabled",
            )
        if prior_attempt_id:
            self._validate_identifier(prior_attempt_id, "PRIOR_ATTEMPT_NOT_FOUND")
        if prior_attempt_id and not use_quality_feedback:
            raise JobStoreError(
                "PRIOR_QUALITY_FEEDBACK_FLAG_REQUIRED",
                "use_quality_feedback must be true when a prior attempt is supplied",
            )
        job_identifier = job_id or uuid.uuid4().hex
        self._validate_identifier(job_identifier, "JOB_ID_INVALID")
        try:
            async with self.database.sessions() as lookup:
                session_id = await lookup.scalar(
                    select(PromptVersion.editing_session_id).where(
                        PromptVersion.id == prompt_version_id
                    )
                )
        except SQLAlchemyError:
            raise JobStoreError(
                "DATABASE_UNAVAILABLE", "prompt version storage is unavailable"
            ) from None
        if session_id is None:
            raise JobStoreError("PROMPT_VERSION_NOT_FOUND", "prompt version not found")

        job_dir = self.store._prepare_job_directories(
            job_identifier,
            include_input=False,
        )
        try:
            source_path, _source_state = await self._ready_source(session_id)
            source_hash = await asyncio.to_thread(_hash_file, source_path)
            async with self._coordination(session_id) as connection:
                async with self._session(connection) as session:
                    async with session.begin():
                        await self._capacity_lock(session)
                        version = await session.scalar(
                            select(PromptVersion)
                            .where(PromptVersion.id == prompt_version_id)
                            .with_for_update()
                        )
                        if version is None:
                            raise JobStoreError(
                                "PROMPT_VERSION_NOT_FOUND", "prompt version not found"
                            )
                        owner, source = await self._locked_workspace(
                            session,
                            version.editing_session_id,
                            source_hash=source_hash,
                        )
                        attempt_number = int(
                            await session.scalar(
                                select(func.coalesce(func.max(VideoJob.attempt_number), 0)).where(
                                    VideoJob.prompt_version_id == version.id
                                )
                            )
                            or 0
                        ) + 1
                        now = _utcnow()
                        quality_feedback = (
                            await self._quality_feedback(
                                session,
                                version=version,
                                prior_attempt_id=str(prior_attempt_id),
                            )
                            if use_quality_feedback
                            else None
                        )
                        job = self._new_job(
                            job_id=job_identifier,
                            owner=owner,
                            source=source,
                            version=version,
                            attempt_number=attempt_number,
                            now=now,
                            quality_feedback=quality_feedback,
                        )
                        session.add(job)
                        await session.flush()
                        await self.store._append_event(
                            session,
                            job,
                            "prompt_run_created",
                            {
                                "prompt_version_id": version.id,
                                "prompt_version_number": version.version_number,
                                "attempt_number": attempt_number,
                                "prior_attempt_id": (
                                    quality_feedback.get("prior_attempt_id")
                                    if quality_feedback
                                    else None
                                ),
                                "quality_feedback_version": (
                                    quality_feedback.get("version")
                                    if quality_feedback
                                    else None
                                ),
                                "retry_reason_codes": (
                                    quality_feedback.get("retry_reason_codes", [])
                                    if quality_feedback
                                    else []
                                ),
                                "resume_policy": (
                                    "reuse_compatible_checkpoints"
                                    if quality_feedback
                                    else None
                                ),
                            },
                        )
        except JobStoreError:
            shutil.rmtree(job_dir, ignore_errors=True)
            raise
        except IntegrityError:
            shutil.rmtree(job_dir, ignore_errors=True)
            raise JobStoreError(
                "PROMPT_RUN_CONFLICT", "prompt run creation conflicted"
            ) from None
        except SQLAlchemyError:
            shutil.rmtree(job_dir, ignore_errors=True)
            raise JobStoreError(
                "DATABASE_UNAVAILABLE", "prompt run storage is unavailable"
            ) from None
        run = await self.store.load(job_identifier)
        await self.store._snapshot(run)
        return run

    async def list_versions(
        self,
        session_id: str,
        *,
        limit: int = 20,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        await self.store.get_session(session_id)
        if not 1 <= int(limit) <= 50:
            raise JobStoreError("PAGE_LIMIT_INVALID", "limit must be between 1 and 50")
        rerun_capability = await self._rerun_capability(session_id)
        boundary = _decode_cursor(cursor)
        query = (
            select(PromptVersion)
            .where(PromptVersion.editing_session_id == session_id)
            .order_by(PromptVersion.created_at.desc(), PromptVersion.id.desc())
            .limit(int(limit) + 1)
        )
        if boundary:
            timestamp, item_id = boundary
            query = query.where(
                (PromptVersion.created_at < timestamp)
                | (
                    (PromptVersion.created_at == timestamp)
                    & (PromptVersion.id < item_id)
                )
            )
        try:
            async with self.database.sessions() as session:
                rows = list((await session.execute(query)).scalars())
                selected = rows[: int(limit)]
                attempts = await self._recent_attempts(
                    session,
                    [row.id for row in selected],
                )
        except SQLAlchemyError:
            raise JobStoreError(
                "DATABASE_UNAVAILABLE", "prompt history is unavailable"
            ) from None
        grouped: dict[str, list[dict[str, Any]]] = {row.id: [] for row in selected}
        for row in attempts:
            grouped[row.prompt_version_id].append(
                self._run_summary(row, rerun_capability=rerun_capability)
            )
        has_more = len(rows) > int(limit)
        return {
            "items": [
                self._version_state(row, attempts=grouped[row.id]) for row in selected
            ],
            "next_cursor": (
                _encode_cursor(selected[-1].created_at, selected[-1].id)
                if has_more and selected
                else None
            ),
        }

    async def get_version(self, prompt_version_id: str) -> dict[str, Any]:
        self._validate_identifier(prompt_version_id, "PROMPT_VERSION_NOT_FOUND")
        try:
            async with self.database.sessions() as session:
                version = await session.get(PromptVersion, prompt_version_id)
                if version is None:
                    raise JobStoreError(
                        "PROMPT_VERSION_NOT_FOUND", "prompt version not found"
                    )
                owner = await session.get(EditingSession, version.editing_session_id)
                if (
                    owner is None
                    or owner.deleted_at is not None
                    or owner.audit_expires_at <= _utcnow()
                ):
                    raise JobStoreError(
                        "PROMPT_VERSION_NOT_FOUND", "prompt version not found"
                    )
                attempts = list(
                    (
                        await session.execute(
                            select(VideoJob)
                            .where(
                                VideoJob.prompt_version_id == prompt_version_id,
                                VideoJob.deleted_at.is_(None),
                            )
                            .order_by(VideoJob.attempt_number.desc(), VideoJob.id.desc())
                            .limit(DETAIL_ATTEMPT_LIMIT)
                        )
                    ).scalars()
                )
                artifacts_by_job: dict[str, list[Artifact]] = {
                    row.id: [] for row in attempts
                }
                if attempts:
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
        except JobStoreError:
            raise
        except SQLAlchemyError:
            raise JobStoreError(
                "DATABASE_UNAVAILABLE", "prompt version is unavailable"
            ) from None
        rerun_capability = await self._rerun_capability(version.editing_session_id)
        return self._version_state(
            version,
            attempts=[
                self._apply_rerun_capability(
                    self.store._job_state(row, artifacts_by_job[row.id]),
                    rerun_capability,
                )
                for row in attempts
            ],
        )

    async def select_favorite(self, session_id: str, run_id: str) -> dict[str, Any]:
        self._validate_identifier(run_id, "FAVORITE_RUN_INVALID")
        changed_ids: set[str] = set()
        try:
            async with self.database.sessions() as session:
                async with session.begin():
                    owner = await session.scalar(
                        select(EditingSession)
                        .where(EditingSession.id == session_id)
                        .with_for_update()
                    )
                    if (
                        owner is None
                        or owner.deleted_at is not None
                        or owner.audit_expires_at <= _utcnow()
                    ):
                        raise JobStoreError("SESSION_NOT_FOUND", "session not found")
                    selected = await session.scalar(
                        select(VideoJob)
                        .where(
                            VideoJob.id == run_id,
                            VideoJob.editing_session_id == session_id,
                            VideoJob.deleted_at.is_(None),
                        )
                        .with_for_update()
                    )
                    if (
                        selected is None
                        or selected.prompt_version_id is None
                        or selected.state != "completed"
                    ):
                        raise JobStoreError(
                            "FAVORITE_RUN_INVALID",
                            "favorite must be a completed run from this session",
                        )
                    previous = list(
                        (
                            await session.execute(
                                select(VideoJob)
                                .where(
                                    VideoJob.editing_session_id == session_id,
                                    VideoJob.is_favorite.is_(True),
                                    VideoJob.id != run_id,
                                )
                                .with_for_update()
                            )
                        ).scalars()
                    )
                    for row in previous:
                        row.is_favorite = False
                        changed_ids.add(row.id)
                        await self.store._append_event(
                            session,
                            row,
                            "run_favorite_cleared",
                            {"selection_source": "human"},
                        )
                    await session.flush()
                    selected.is_favorite = True
                    now = _utcnow()
                    selected.updated_at = now
                    owner.updated_at = now
                    changed_ids.add(selected.id)
                    await self.store._append_event(
                        session,
                        selected,
                        "run_favorite_selected",
                        {"selection_source": "human"},
                    )
        except JobStoreError:
            raise
        except (IntegrityError, SQLAlchemyError):
            raise JobStoreError(
                "DATABASE_UNAVAILABLE", "favorite selection is unavailable"
            ) from None
        await self._snapshot_jobs(changed_ids)
        return {
            "editing_session_id": session_id,
            "favorite_run_id": run_id,
            "selection_source": "human",
        }

    async def clear_favorite(self, session_id: str) -> dict[str, Any]:
        changed_ids: set[str] = set()
        try:
            async with self.database.sessions() as session:
                async with session.begin():
                    owner = await session.scalar(
                        select(EditingSession)
                        .where(EditingSession.id == session_id)
                        .with_for_update()
                    )
                    if (
                        owner is None
                        or owner.deleted_at is not None
                        or owner.audit_expires_at <= _utcnow()
                    ):
                        raise JobStoreError("SESSION_NOT_FOUND", "session not found")
                    favorites = list(
                        (
                            await session.execute(
                                select(VideoJob)
                                .where(
                                    VideoJob.editing_session_id == session_id,
                                    VideoJob.is_favorite.is_(True),
                                )
                                .with_for_update()
                            )
                        ).scalars()
                    )
                    for row in favorites:
                        row.is_favorite = False
                        row.updated_at = _utcnow()
                        changed_ids.add(row.id)
                        await self.store._append_event(
                            session,
                            row,
                            "run_favorite_cleared",
                            {"selection_source": "human"},
                        )
                    owner.updated_at = _utcnow()
        except JobStoreError:
            raise
        except SQLAlchemyError:
            raise JobStoreError(
                "DATABASE_UNAVAILABLE", "favorite selection is unavailable"
            ) from None
        await self._snapshot_jobs(changed_ids)
        return {
            "editing_session_id": session_id,
            "favorite_run_id": None,
            "selection_source": "human",
        }

    async def _locked_workspace(
        self,
        session: AsyncSession,
        session_id: str,
        *,
        source_hash: str,
    ) -> tuple[EditingSession, SessionInputVideo]:
        now = _utcnow()
        owner = await session.scalar(
            select(EditingSession)
            .where(EditingSession.id == session_id)
            .with_for_update()
        )
        if (
            owner is None
            or owner.deleted_at is not None
            or owner.audit_expires_at <= now
        ):
            raise JobStoreError("SESSION_NOT_FOUND", "session not found")
        if owner.workflow_version != 2:
            raise JobStoreError(
                "SESSION_WORKFLOW_LEGACY",
                "create a reusable session before creating prompt versions",
            )
        source = await session.scalar(
            select(SessionInputVideo)
            .where(SessionInputVideo.editing_session_id == session_id)
            .with_for_update()
        )
        if (
            source is None
            or source.state != "ready"
            or source.purged_at is not None
            or source.expires_at is None
            or source.expires_at <= now
            or not source.relative_path
            or not source.sha256
        ):
            raise JobStoreError("SESSION_SOURCE_UNAVAILABLE", "session source is unavailable")
        if source.sha256 != source_hash:
            raise JobStoreError("SESSION_SOURCE_CHANGED", "session source identity changed")
        owner.updated_at = now
        owner.audit_expires_at = max(
            owner.audit_expires_at,
            now + self.store.audit_retention,
        )
        source.expires_at = now + self.store.media_retention
        source.updated_at = now
        return owner, source

    async def _capacity_lock(self, session: AsyncSession) -> None:
        await session.execute(
            text("SELECT pg_advisory_xact_lock(:key)"),
            {"key": CAPACITY_ADVISORY_LOCK},
        )
        active = await session.scalar(
            select(func.count())
            .select_from(VideoJob)
            .where(VideoJob.state.in_(ACTIVE_STATES))
        )
        if int(active or 0) >= self.store.max_active_jobs:
            raise JobStoreError("JOB_QUEUE_FULL", "job queue is at capacity")

    def _new_job(
        self,
        *,
        job_id: str,
        owner: EditingSession,
        source: SessionInputVideo,
        version: PromptVersion,
        attempt_number: int,
        now: datetime,
        quality_feedback: dict[str, Any] | None = None,
    ) -> VideoJob:
        request_data = dict(version.settings_data or {})
        if quality_feedback:
            request_data["prior_attempt_quality_feedback"] = quality_feedback
            request_data.update({
                "retry_of_attempt_id": quality_feedback.get("prior_attempt_id"),
                "retry_reason_codes": quality_feedback.get("retry_reason_codes", []),
                "resume_policy": "reuse_compatible_checkpoints",
                "prior_outcome_grade": quality_feedback.get("prior_outcome_grade"),
            })
        return VideoJob(
            id=job_id,
            editing_session_id=owner.id,
            prompt_version_id=version.id,
            attempt_number=attempt_number,
            state="queued",
            progress=Decimal("0.05"),
            prompt=version.prompt,
            request_data=request_data,
            input_data={
                "source_kind": "session_input_video",
                "input_video_id": source.id,
                "original_filename": source.original_filename,
                "stored_filename": "",
                "relative_path": source.relative_path,
                "size": int(source.expected_size),
                "sha256": source.sha256,
                "media_type": source.media_type,
            },
            result_data={},
            updated_at=now,
            media_expires_at=now + self.store.media_retention,
            audit_expires_at=now + self.store.audit_retention,
        )

    async def _quality_feedback(
        self,
        session: AsyncSession,
        *,
        version: PromptVersion,
        prior_attempt_id: str,
    ) -> dict[str, Any]:
        prior = await session.scalar(
            select(VideoJob).where(
                VideoJob.id == prior_attempt_id,
                VideoJob.prompt_version_id == version.id,
                VideoJob.deleted_at.is_(None),
                VideoJob.audit_expires_at > _utcnow(),
            )
        )
        if prior is None:
            raise JobStoreError("PRIOR_ATTEMPT_NOT_FOUND", "prior attempt not found")
        if prior.state in ACTIVE_STATES:
            raise JobStoreError(
                "PRIOR_ATTEMPT_NOT_READY",
                "prior attempt quality evidence is not ready",
            )
        rows = list((await session.execute(
            select(AuditDocument)
            .where(
                AuditDocument.job_id == prior.id,
                AuditDocument.parse_status == "parsed",
            )
            .order_by(AuditDocument.created_at.desc(), AuditDocument.id.desc())
        )).scalars())
        documents: dict[str, Any] = {}
        for row in rows:
            if row.source_name in documents or not isinstance(row.parsed_data, dict):
                continue
            if row.source_name in {
                "render_promotion.json",
                "frame_quality_qa.json",
                "clip_visual_coverage.json",
                "creative_conformance.json",
                "outcome_report.json",
                "fallback_ledger.json",
            } or row.source_name.endswith(".caption-footprint.json"):
                documents[row.source_name] = dict(row.parsed_data)
        if not documents:
            raise JobStoreError(
                "PRIOR_QUALITY_EVIDENCE_UNAVAILABLE",
                "prior attempt has no eligible deterministic quality evidence",
            )
        feedback = compact_prior_attempt_quality_feedback(
            prior_attempt_id=prior.id,
            prior_attempt_number=int(prior.attempt_number or 1),
            documents=documents,
        )
        if not feedback.get("blocker_codes") and not feedback.get("retry_reason_codes"):
            raise JobStoreError(
                "PRIOR_QUALITY_EVIDENCE_UNAVAILABLE",
                "prior attempt has no eligible deterministic quality evidence",
            )
        return feedback

    async def _recent_attempts(
        self,
        session: AsyncSession,
        version_ids: list[str],
    ) -> list[VideoJob]:
        if not version_ids:
            return []
        ranked = (
            select(
                VideoJob.id.label("job_id"),
                func.row_number()
                .over(
                    partition_by=VideoJob.prompt_version_id,
                    order_by=(VideoJob.attempt_number.desc(), VideoJob.id.desc()),
                )
                .label("attempt_rank"),
            )
            .where(
                VideoJob.prompt_version_id.in_(version_ids),
                VideoJob.deleted_at.is_(None),
            )
            .subquery()
        )
        return list(
            (
                await session.execute(
                    select(VideoJob)
                    .join(ranked, ranked.c.job_id == VideoJob.id)
                    .where(ranked.c.attempt_rank <= RECENT_ATTEMPTS_PER_VERSION)
                    .order_by(
                        VideoJob.prompt_version_id,
                        VideoJob.attempt_number.desc(),
                    )
                )
            ).scalars()
        )

    async def _snapshot_jobs(self, job_ids: set[str]) -> None:
        for job_id in sorted(job_ids):
            try:
                await self.store._snapshot(await self.store.load_for_audit(job_id))
            except JobStoreError:
                continue

    @asynccontextmanager
    async def _coordination(
        self,
        session_id: str,
    ) -> AsyncIterator[AsyncConnection]:
        async with self.session_media.coordination(
            session_id,
            wait=True,
        ) as connection:
            yield connection

    async def _ready_source(self, session_id: str) -> tuple[Path, dict[str, Any]]:
        async with self._coordination(session_id) as connection:
            return await self.session_media.resolve_ready_coordinated(
                session_id,
                connection=connection,
            )

    async def _rerun_capability(self, session_id: str) -> dict[str, Any]:
        try:
            await self._ready_source(session_id)
        except JobStoreError as exc:
            if exc.code not in RERUN_UNAVAILABLE_CODES:
                raise
            return {
                "supported": False,
                "unavailable_reason": exc.code,
            }
        return {"supported": True, "unavailable_reason": ""}

    @staticmethod
    def _validate_identifier(value: str, code: str) -> None:
        if not JOB_ID_PATTERN.fullmatch(str(value or "")):
            raise JobStoreError(code, "identifier is invalid")

    @staticmethod
    def _run_summary(
        row: VideoJob,
        *,
        rerun_capability: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        result_data = row.result_data if isinstance(row.result_data, dict) else {}
        request_data = row.request_data if isinstance(row.request_data, dict) else {}
        state = {
            "id": row.id,
            "attempt_number": row.attempt_number,
            "state": row.state,
            "stage": row.stage,
            "progress": float(row.progress),
            "is_favorite": row.is_favorite,
            "error_code": (
                (row.error_data or {}).get("code")
                if isinstance(row.error_data, dict)
                else None
            ),
            "outcome": outcome_summary(result_data.get("outcome")),
            "retry_of_attempt_id": request_data.get("retry_of_attempt_id"),
            "created_at": _iso(row.created_at),
            "completed_at": _iso(row.completed_at),
            "media_expires_at": _iso(row.media_expires_at),
        }
        return PromptVersionService._apply_rerun_capability(
            state,
            rerun_capability,
        )

    @staticmethod
    def _apply_rerun_capability(
        run: dict[str, Any],
        capability: dict[str, Any] | None,
    ) -> dict[str, Any]:
        if capability is None or not isinstance(run.get("outcome"), dict):
            return run
        state = dict(run)
        outcome = dict(state["outcome"])
        retry = dict(outcome.get("retry") or {})
        retry["supported"] = capability.get("supported") is True
        retry["unavailable_reason"] = str(
            capability.get("unavailable_reason") or ""
        )[:80]
        if not retry["supported"]:
            retry["recommended_action"] = "none"
        elif retry.get("quality_feedback_supported"):
            retry["recommended_action"] = "retry_defects"
        else:
            retry["recommended_action"] = "rerun"
        outcome["retry"] = retry
        state["outcome"] = outcome
        return state

    @classmethod
    def _version_state(
        cls,
        row: PromptVersion,
        *,
        attempts: list[dict[str, Any]],
    ) -> dict[str, Any]:
        return {
            "id": row.id,
            "editing_session_id": row.editing_session_id,
            "version_number": row.version_number,
            "prompt": row.prompt,
            "settings": dict(row.settings_data or {}),
            "created_at": _iso(row.created_at),
            "attempts": attempts,
        }

    @staticmethod
    def _session(connection: AsyncConnection) -> AsyncSession:
        return AsyncSession(bind=connection, expire_on_commit=False)
