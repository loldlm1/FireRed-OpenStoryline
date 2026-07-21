from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
import asyncio
import hashlib
import json
import os
import re
import uuid

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError

from open_storyline.mvp.models import (
    JobStageCheckpoint,
    SessionAnalysisCache,
    SessionInputVideo,
    VideoJob,
)


CHECKPOINT_PAYLOAD_VERSION = "pipeline_checkpoint.v1"
CHECKPOINT_MAX_BYTES = 16 * 1024 * 1024
STAGE_PATTERN = re.compile(r"^[a-z0-9_]{1,64}$")


class CheckpointError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"{code}: {message}")


@dataclass(frozen=True)
class CheckpointHit:
    stage: str
    fingerprint: str
    payload: dict[str, Any]
    sha256: str
    source_job_id: str | None = None


def checkpoint_fingerprint(value: dict[str, Any]) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def checkpoints_enabled() -> bool:
    raw = os.getenv("OPENSTORYLINE_CHECKPOINTS_ENABLED", "false").strip().lower()
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off", ""}:
        return False
    raise CheckpointError(
        "CHECKPOINT_CONFIG_INVALID",
        "OPENSTORYLINE_CHECKPOINTS_ENABLED must be true or false",
    )


class CheckpointStore:
    def __init__(self, store: Any, *, enabled: bool | None = None) -> None:
        self.store = store
        self.enabled = checkpoints_enabled() if enabled is None else bool(enabled)
        if not hasattr(store, "database"):
            self.enabled = False

    async def load_session(
        self,
        *,
        editing_session_id: str,
        input_video_id: str,
        stage: str,
        fingerprint: str,
    ) -> CheckpointHit | None:
        if not self.enabled:
            return None
        self._validate_keys(stage, fingerprint)
        try:
            async with self.store.database.sessions() as session:
                row = await session.scalar(
                    select(SessionAnalysisCache).where(
                        SessionAnalysisCache.editing_session_id == editing_session_id,
                        SessionAnalysisCache.input_video_id == input_video_id,
                        SessionAnalysisCache.stage == stage,
                        SessionAnalysisCache.fingerprint == fingerprint,
                        SessionAnalysisCache.status == "available",
                        SessionAnalysisCache.expires_at > self._now(),
                    )
                )
        except SQLAlchemyError:
            raise CheckpointError(
                "CHECKPOINT_DATABASE_UNAVAILABLE",
                "session checkpoint storage is unavailable",
            ) from None
        if row is None:
            return None
        return await self._read_row(
            row,
            root=Path(self.store.session_media_root).resolve(),
            stage=stage,
            fingerprint=fingerprint,
            model=SessionAnalysisCache,
        )

    async def save_session(
        self,
        *,
        editing_session_id: str,
        input_video_id: str,
        stage: str,
        contract_version: str,
        fingerprint: str,
        payload: dict[str, Any],
        metadata: dict[str, Any] | None = None,
    ) -> CheckpointHit | None:
        if not self.enabled:
            return None
        self._validate_keys(stage, fingerprint)
        root = Path(self.store.session_media_root).resolve()
        relative = Path(editing_session_id) / "analysis" / f"{stage}-{fingerprint}.json"
        path = self._validated_path(root, relative.as_posix())
        digest, size = await asyncio.to_thread(
            self._write_envelope,
            path,
            stage=stage,
            fingerprint=fingerprint,
            payload=payload,
        )
        now = self._now()
        try:
            async with self.store.database.sessions() as session:
                async with session.begin():
                    source = await session.scalar(
                        select(SessionInputVideo).where(
                            SessionInputVideo.id == input_video_id,
                            SessionInputVideo.editing_session_id == editing_session_id,
                        )
                    )
                    if source is None or source.expires_at is None:
                        raise CheckpointError(
                            "CHECKPOINT_SOURCE_UNAVAILABLE",
                            "session source is unavailable for checkpointing",
                        )
                    statement = insert(SessionAnalysisCache).values(
                        editing_session_id=editing_session_id,
                        input_video_id=input_video_id,
                        stage=stage,
                        contract_version=str(contract_version)[:64],
                        fingerprint=fingerprint,
                        relative_path=relative.as_posix(),
                        sha256=digest,
                        byte_size=size,
                        status="available",
                        metadata_data=dict(metadata or {}),
                        updated_at=now,
                        expires_at=source.expires_at,
                    ).on_conflict_do_update(
                        index_elements=["input_video_id", "stage", "fingerprint"],
                        set_={
                            "editing_session_id": editing_session_id,
                            "contract_version": str(contract_version)[:64],
                            "relative_path": relative.as_posix(),
                            "sha256": digest,
                            "byte_size": size,
                            "status": "available",
                            "metadata_data": dict(metadata or {}),
                            "updated_at": now,
                            "expires_at": source.expires_at,
                        },
                    )
                    await session.execute(statement)
        except CheckpointError:
            path.unlink(missing_ok=True)
            raise
        except SQLAlchemyError:
            path.unlink(missing_ok=True)
            raise CheckpointError(
                "CHECKPOINT_DATABASE_UNAVAILABLE",
                "session checkpoint storage is unavailable",
            ) from None
        return CheckpointHit(stage, fingerprint, dict(payload), digest)

    async def load_job(
        self,
        *,
        job_id: str,
        stage: str,
        fingerprint: str,
    ) -> CheckpointHit | None:
        if not self.enabled:
            return None
        self._validate_keys(stage, fingerprint)
        try:
            async with self.store.database.sessions() as session:
                row = await session.scalar(
                    select(JobStageCheckpoint).where(
                        JobStageCheckpoint.job_id == job_id,
                        JobStageCheckpoint.stage == stage,
                        JobStageCheckpoint.fingerprint == fingerprint,
                        JobStageCheckpoint.status == "available",
                        JobStageCheckpoint.expires_at > self._now(),
                    )
                )
        except SQLAlchemyError:
            raise CheckpointError(
                "CHECKPOINT_DATABASE_UNAVAILABLE",
                "job checkpoint storage is unavailable",
            ) from None
        if row is None:
            return None
        return await self._read_row(
            row,
            root=Path(self.store.root).resolve(),
            stage=stage,
            fingerprint=fingerprint,
            model=JobStageCheckpoint,
            source_job_id=job_id,
        )

    async def save_job(
        self,
        *,
        job_id: str,
        stage: str,
        contract_version: str,
        fingerprint: str,
        payload: dict[str, Any],
        metadata: dict[str, Any] | None = None,
        reused_from_job_id: str | None = None,
    ) -> CheckpointHit | None:
        if not self.enabled:
            return None
        self._validate_keys(stage, fingerprint)
        root = Path(self.store.root).resolve()
        relative = Path(job_id) / "output" / f".checkpoint-{stage}-{fingerprint}.json"
        path = self._validated_path(root, relative.as_posix())
        digest, size = await asyncio.to_thread(
            self._write_envelope,
            path,
            stage=stage,
            fingerprint=fingerprint,
            payload=payload,
        )
        now = self._now()
        try:
            async with self.store.database.sessions() as session:
                async with session.begin():
                    job = await session.get(VideoJob, job_id)
                    if job is None:
                        raise CheckpointError(
                            "CHECKPOINT_JOB_UNAVAILABLE",
                            "job is unavailable for checkpointing",
                        )
                    statement = insert(JobStageCheckpoint).values(
                        job_id=job_id,
                        stage=stage,
                        contract_version=str(contract_version)[:64],
                        fingerprint=fingerprint,
                        relative_path=relative.as_posix(),
                        sha256=digest,
                        byte_size=size,
                        status="available",
                        reused_from_job_id=reused_from_job_id,
                        metadata_data=dict(metadata or {}),
                        updated_at=now,
                        expires_at=job.audit_expires_at,
                    ).on_conflict_do_update(
                        index_elements=["job_id", "stage", "fingerprint"],
                        set_={
                            "contract_version": str(contract_version)[:64],
                            "relative_path": relative.as_posix(),
                            "sha256": digest,
                            "byte_size": size,
                            "status": "available",
                            "reused_from_job_id": reused_from_job_id,
                            "metadata_data": dict(metadata or {}),
                            "updated_at": now,
                            "expires_at": job.audit_expires_at,
                        },
                    )
                    await session.execute(statement)
        except CheckpointError:
            path.unlink(missing_ok=True)
            raise
        except SQLAlchemyError:
            path.unlink(missing_ok=True)
            raise CheckpointError(
                "CHECKPOINT_DATABASE_UNAVAILABLE",
                "job checkpoint storage is unavailable",
            ) from None
        return CheckpointHit(
            stage,
            fingerprint,
            dict(payload),
            digest,
            source_job_id=reused_from_job_id,
        )

    async def _read_row(
        self,
        row: Any,
        *,
        root: Path,
        stage: str,
        fingerprint: str,
        model: Any,
        source_job_id: str | None = None,
    ) -> CheckpointHit | None:
        try:
            path = self._validated_path(root, row.relative_path)
            raw = await asyncio.to_thread(path.read_bytes)
            if len(raw) > CHECKPOINT_MAX_BYTES or len(raw) != int(row.byte_size):
                raise CheckpointError(
                    "CHECKPOINT_SIZE_MISMATCH", "checkpoint size is invalid"
                )
            digest = hashlib.sha256(raw).hexdigest()
            if digest != row.sha256:
                raise CheckpointError(
                    "CHECKPOINT_HASH_MISMATCH", "checkpoint hash is invalid"
                )
            envelope = json.loads(raw)
            if (
                not isinstance(envelope, dict)
                or envelope.get("version") != CHECKPOINT_PAYLOAD_VERSION
                or envelope.get("stage") != stage
                or envelope.get("fingerprint") != fingerprint
                or not isinstance(envelope.get("payload"), dict)
            ):
                raise CheckpointError(
                    "CHECKPOINT_PAYLOAD_INVALID", "checkpoint payload is invalid"
                )
        except (OSError, json.JSONDecodeError, CheckpointError) as exc:
            code = getattr(exc, "code", "CHECKPOINT_FILE_UNAVAILABLE")
            await self._quarantine(model, row.id, str(code))
            return None
        return CheckpointHit(
            stage,
            fingerprint,
            dict(envelope["payload"]),
            digest,
            source_job_id=source_job_id,
        )

    async def _quarantine(self, model: Any, row_id: int, code: str) -> None:
        try:
            async with self.store.database.sessions() as session:
                async with session.begin():
                    await session.execute(
                        update(model)
                        .where(model.id == row_id)
                        .values(
                            status="quarantined",
                            updated_at=self._now(),
                            metadata_data={"quarantine_code": str(code)[:80]},
                        )
                    )
        except SQLAlchemyError:
            return

    @staticmethod
    def _write_envelope(
        path: Path,
        *,
        stage: str,
        fingerprint: str,
        payload: dict[str, Any],
    ) -> tuple[str, int]:
        envelope = {
            "version": CHECKPOINT_PAYLOAD_VERSION,
            "stage": stage,
            "fingerprint": fingerprint,
            "payload": payload,
        }
        raw = json.dumps(
            envelope,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        if len(raw) > CHECKPOINT_MAX_BYTES:
            raise CheckpointError(
                "CHECKPOINT_TOO_LARGE", "checkpoint payload exceeds the size limit"
            )
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        try:
            with temporary.open("xb") as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
        return hashlib.sha256(raw).hexdigest(), len(raw)

    @staticmethod
    def _validated_path(root: Path, relative_path: str) -> Path:
        relative = Path(str(relative_path or ""))
        if not relative.parts or relative.is_absolute() or ".." in relative.parts:
            raise CheckpointError(
                "CHECKPOINT_PATH_INVALID", "checkpoint path is invalid"
            )
        current = root
        for part in relative.parts[:-1]:
            current /= part
            if current.is_symlink():
                raise CheckpointError(
                    "CHECKPOINT_PATH_INVALID", "checkpoint path is invalid"
                )
        path = root / relative
        resolved = path.resolve(strict=False)
        if root not in resolved.parents:
            raise CheckpointError(
                "CHECKPOINT_PATH_INVALID", "checkpoint path is invalid"
            )
        return resolved

    @staticmethod
    def _validate_keys(stage: str, fingerprint: str) -> None:
        if not STAGE_PATTERN.fullmatch(str(stage or "")):
            raise CheckpointError(
                "CHECKPOINT_STAGE_INVALID", "checkpoint stage is invalid"
            )
        if not re.fullmatch(r"[a-f0-9]{64}", str(fingerprint or "")):
            raise CheckpointError(
                "CHECKPOINT_FINGERPRINT_INVALID", "checkpoint fingerprint is invalid"
            )

    @staticmethod
    def _now() -> datetime:
        return datetime.now(UTC)
