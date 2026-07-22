from __future__ import annotations

from datetime import UTC, datetime, timedelta
from fractions import Fraction
from pathlib import Path
from typing import Any, Awaitable, Callable
import asyncio
import base64
import binascii
import hashlib
import json
import math
import os
import re
import subprocess

from sqlalchemy import and_, exists, func, or_, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from open_storyline.mvp.jobs import JOB_ID_PATTERN, JobStore, JobStoreError, _iso
from open_storyline.mvp.models import (
    Artifact,
    AuditDocument,
    AuditReview,
    EditingSession,
    SessionInputVideo,
    VideoJob,
)
from open_storyline.mvp.defects import RepairStrategy, defect_public_metadata
from open_storyline.mvp.outcomes import (
    build_outcome_slo_summary,
    outcome_summary,
    repair_defect_lifecycle,
)
from open_storyline.mvp.repair import RepairContractError, validate_repair_report
from open_storyline.mvp.security import (
    sanitize_audit_document,
    sanitize_for_persistence,
    sanitize_text,
)


AUDIT_PARSER_VERSION = "1"
AUDIT_DEFECT_DISPOSITIONS = frozenset({
    "fallback_applied",
    "new",
    "not_repairable",
    "remaining",
    "resolved",
})
AUDIT_DEFECT_SOURCES = frozenset({
    "fallback_ledger.json",
    "outcome_report.json",
    "render_promotion.json",
    "repair_report.json",
})
AUDIT_FILTER_TOKEN = re.compile(r"^[A-Za-z0-9._:-]{1,120}$")
SRT_TIME = re.compile(
    r"^(\d{2}):(\d{2}):(\d{2})[,.](\d{3})\s+-->\s+"
    r"(\d{2}):(\d{2}):(\d{2})[,.](\d{3})$"
)


def _bounded_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)).strip())
    except ValueError:
        raise JobStoreError("AUDIT_CONFIG_INVALID", f"{name} must be an integer") from None
    if not minimum <= value <= maximum:
        raise JobStoreError(
            "AUDIT_CONFIG_INVALID",
            f"{name} must be between {minimum} and {maximum}",
        )
    return value


def _hash_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _marker_hash(job_id: str, source_name: str, code: str) -> str:
    return _hash_bytes(f"{job_id}:{source_name}:{code}".encode("utf-8"))


def _read_bounded(path: Path, limit: int) -> tuple[bytes, bool]:
    with path.open("rb") as stream:
        value = stream.read(limit + 1)
    return value[:limit], len(value) > limit


def _srt_milliseconds(groups: tuple[str, ...]) -> int:
    hours, minutes, seconds, milliseconds = (int(value) for value in groups)
    if minutes >= 60 or seconds >= 60:
        raise ValueError("invalid clock")
    return ((hours * 60 + minutes) * 60 + seconds) * 1000 + milliseconds


def parse_srt(value: str) -> tuple[dict[str, Any] | None, str | None]:
    blocks = [item.strip() for item in re.split(r"\r?\n\s*\r?\n", value) if item.strip()]
    cues: list[tuple[int, int, str]] = []
    invalid = 0
    ordering_errors = 0
    previous_end = -1
    for block in blocks[:10_000]:
        lines = block.splitlines()
        if len(lines) < 2:
            invalid += 1
            continue
        timing_index = 1 if lines[0].strip().isdigit() else 0
        if timing_index >= len(lines):
            invalid += 1
            continue
        match = SRT_TIME.fullmatch(lines[timing_index].strip())
        if match is None:
            invalid += 1
            continue
        try:
            start = _srt_milliseconds(match.groups()[:4])
            end = _srt_milliseconds(match.groups()[4:])
        except ValueError:
            invalid += 1
            continue
        if end <= start:
            invalid += 1
            continue
        if start < previous_end:
            ordering_errors += 1
        previous_end = max(previous_end, end)
        text_value = "\n".join(lines[timing_index + 1 :]).strip()
        cues.append((start, end, text_value))
    if not cues:
        return None, "SRT_INVALID"
    summary = {
        "cue_count": len(cues),
        "first_start_ms": cues[0][0],
        "last_end_ms": max(item[1] for item in cues),
        "text_length": sum(len(item[2]) for item in cues),
        "ordering_errors": ordering_errors,
        "invalid_cues": invalid,
        "truncated": len(blocks) > 10_000,
    }
    return summary, None if invalid == 0 else "SRT_INVALID_CUES"


def _parse_document(source_name: str, raw_text: str) -> tuple[Any, str, str | None]:
    suffix = Path(source_name).suffix.lower()
    if suffix == ".json":
        try:
            return json.loads(raw_text), "parsed", None
        except json.JSONDecodeError:
            return None, "invalid", "JSON_INVALID"
    if suffix == ".srt":
        parsed, error = parse_srt(raw_text)
        return parsed, "parsed" if parsed is not None else "invalid", error
    return None, "invalid", "DOCUMENT_TYPE_UNSUPPORTED"


def _parse_json_document(raw_text: str) -> Any:
    def reject_constant(_value: str) -> None:
        raise ValueError("non-finite JSON number")

    def finite_float(value: str) -> float:
        parsed = float(value)
        if not math.isfinite(parsed):
            raise ValueError("non-finite JSON number")
        return parsed

    return json.loads(
        raw_text,
        parse_constant=reject_constant,
        parse_float=finite_float,
    )


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


def parse_since(value: str | None) -> datetime | None:
    if not value:
        return None
    match = re.fullmatch(r"([1-9][0-9]*)([hd])", value.strip().lower())
    if match:
        amount = int(match.group(1))
        maximum = 87_600 if match.group(2) == "h" else 3_650
        if amount > maximum:
            raise JobStoreError("AUDIT_FILTER_INVALID", "since window is too large")
        return datetime.now(UTC) - (
            timedelta(hours=amount) if match.group(2) == "h" else timedelta(days=amount)
        )
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise JobStoreError("AUDIT_FILTER_INVALID", "since must be an ISO time or values like 24h") from None
    if parsed.tzinfo is None:
        raise JobStoreError("AUDIT_FILTER_INVALID", "since must include a timezone")
    return parsed.astimezone(UTC)


def _defect_filter(name: str, value: str | None, *, allowed: set[str] | frozenset[str] | None = None) -> str | None:
    if value is None:
        return None
    normalized = value.strip().lower() if name != "code" else value.strip().upper()
    if not AUDIT_FILTER_TOKEN.fullmatch(normalized) or (
        allowed is not None and normalized not in allowed
    ):
        raise JobStoreError("AUDIT_FILTER_INVALID", f"{name} filter is invalid")
    return normalized


def _audit_token(value: Any, *, limit: int = 120, fallback: str = "") -> str:
    candidate = str(value or "")[:limit]
    return candidate if AUDIT_FILTER_TOKEN.fullmatch(candidate) else fallback


def _defect_records_from_evidence(
    *,
    job_id: str,
    attempt_number: int | None,
    created_at: datetime,
    completed_at: datetime | None,
    outcome: Any,
    repair_report: Any = None,
    promotion_report: Any = None,
    fallback_ledger: Any = None,
) -> list[dict[str, Any]]:
    compact = outcome_summary(outcome) or {}
    repair = compact.get("repair") if isinstance(compact.get("repair"), dict) else {}
    defects = [
        item for item in (repair.get("defects") or [])[:64]
        if isinstance(item, dict)
    ]
    if not defects and isinstance(repair_report, dict):
        defects = repair_defect_lifecycle(repair_report)
    repair_document = repair_report if isinstance(repair_report, dict) else {}
    promotion = promotion_report if isinstance(promotion_report, dict) else {}
    policy_decisions = (
        promotion.get("policy_decisions")
        if isinstance(promotion.get("policy_decisions"), dict)
        else {}
    )
    strict = compact.get("strict_qa") if isinstance(compact.get("strict_qa"), dict) else {}
    delivery = compact.get("delivery") if isinstance(compact.get("delivery"), dict) else {}
    technical_status = _audit_token(
        compact.get("technical_status")
        or promotion.get("technical_status")
        or "unknown",
        limit=40,
        fallback="unknown",
    )
    strict_decision = _audit_token(
        strict.get("decision")
        or promotion.get("strict_decision")
        or policy_decisions.get("strict")
        or "unknown",
        limit=40,
        fallback="unknown",
    )
    delivery_decision = _audit_token(
        delivery.get("decision")
        or promotion.get("delivery_decision")
        or "unknown",
        limit=40,
        fallback="unknown",
    )
    registry_version = _audit_token(
        compact.get("registry_version")
        or repair_document.get("registry_version")
        or "unknown",
        limit=80,
        fallback="unknown",
    )
    timestamp = _iso(completed_at or created_at)
    records: dict[tuple[str, str, str], dict[str, Any]] = {}

    def add(
        *,
        code: Any,
        stage: Any,
        disposition: str,
        strategy: Any = None,
        repair_attempted: bool = False,
        repair_state: Any = None,
        checkpoint_reused: bool = False,
        fallback_requested: Any = None,
        fallback_executed: Any = None,
    ) -> None:
        presentation = defect_public_metadata(code)
        raw_code = presentation["raw_code"]
        stage_name = _audit_token(stage, limit=64, fallback="unknown")
        key = (raw_code, stage_name, disposition)
        records[key] = sanitize_for_persistence({
            "job_id": job_id,
            "attempt_id": job_id,
            "attempt_number": attempt_number,
            "registry_version": registry_version,
            "code": raw_code,
            "canonical_code": presentation["canonical_code"],
            "registered": presentation["registered"],
            "stage": stage_name,
            "strategy": _audit_token(
                strategy or presentation["repair_strategy"],
                limit=80,
                fallback="terminal",
            ),
            "disposition": disposition,
            "repair_attempted": repair_attempted,
            "repair_state": _audit_token(
                repair_state,
                limit=40,
                fallback="unknown",
            ),
            "checkpoint_reused": checkpoint_reused,
            "fallback_applied": disposition == "fallback_applied",
            "fallback_requested": _audit_token(fallback_requested),
            "fallback_executed": _audit_token(fallback_executed),
            "technical_status": technical_status,
            "strict_decision": strict_decision,
            "delivery_decision": delivery_decision,
            "created_at": _iso(created_at),
            "completed_at": _iso(completed_at),
            "timestamp": timestamp,
        })

    for defect in defects:
        stages = [
            item for item in (defect.get("stage_statuses") or [])[:4]
            if isinstance(item, dict)
        ] or [{"stage": "repair", "status": "unknown", "checkpoint_reused": False}]
        dispositions = [
            item for item in defect.get("dispositions") or ()
            if item in AUDIT_DEFECT_DISPOSITIONS
        ] or ["remaining"]
        fallbacks = [
            item for item in (defect.get("fallbacks") or [])[:8]
            if isinstance(item, dict)
        ]
        for stage in stages:
            for disposition in dispositions:
                fallback = fallbacks[0] if disposition == "fallback_applied" and fallbacks else {}
                add(
                    code=defect.get("code"),
                    stage=stage.get("stage"),
                    disposition=disposition,
                    strategy=defect.get("strategy"),
                    repair_attempted=defect.get("repair_attempted") is True,
                    repair_state=stage.get("status"),
                    checkpoint_reused=stage.get("checkpoint_reused") is True,
                    fallback_requested=fallback.get("requested"),
                    fallback_executed=fallback.get("executed"),
                )

    for field in ("limitations", "fatal_errors"):
        for finding in (compact.get(field) or [])[:24]:
            if not isinstance(finding, dict):
                continue
            add(
                code=finding.get("code"),
                stage=finding.get("stage") or "qa",
                disposition="remaining",
                fallback_requested=finding.get("requested"),
                fallback_executed=finding.get("executed"),
            )
            if finding.get("executed"):
                add(
                    code=finding.get("code"),
                    stage=finding.get("stage") or "compile",
                    disposition="fallback_applied",
                    fallback_requested=finding.get("requested"),
                    fallback_executed=finding.get("executed"),
                )

    ledger = fallback_ledger if isinstance(fallback_ledger, dict) else {}
    for fallback in (ledger.get("entries") or [])[:64]:
        if not isinstance(fallback, dict):
            continue
        add(
            code=fallback.get("code"),
            stage="compile",
            disposition="fallback_applied",
            fallback_requested=fallback.get("requested"),
            fallback_executed=fallback.get("executed"),
        )
    return [records[key] for key in sorted(records)]


def _probe_video(path: Path) -> dict[str, Any]:
    try:
        completed = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                (
                    "stream=codec_type,codec_name,width,height,duration,"
                    "avg_frame_rate,r_frame_rate:format=duration"
                ),
                "-of",
                "json",
                str(path),
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=120,
        )
    except FileNotFoundError as exc:
        raise JobStoreError("FFPROBE_UNAVAILABLE", "FFprobe is unavailable") from exc
    except subprocess.TimeoutExpired as exc:
        raise JobStoreError("FFPROBE_TIMEOUT", "FFprobe timed out") from exc
    if completed.returncode != 0:
        raise JobStoreError("VIDEO_PROBE_FAILED", "the output video is not decodable")
    try:
        payload = json.loads(completed.stdout)
        streams = list(payload.get("streams") or [])
        video = next(item for item in streams if item.get("codec_type") == "video")
        format_data = payload.get("format") or {}
        duration = float(format_data.get("duration") or video.get("duration"))
        rate_value = str(video.get("avg_frame_rate") or video.get("r_frame_rate") or "0")
        frame_rate = float(Fraction(rate_value)) if rate_value not in {"0", "0/0"} else 0.0
        result = {
            "duration_ms": int(round(duration * 1000)),
            "width": int(video["width"]),
            "height": int(video["height"]),
            "frame_rate": round(frame_rate, 3),
            "video_codec": sanitize_text(video.get("codec_name"), limit=80),
            "audio_codecs": [
                sanitize_text(item.get("codec_name"), limit=80)
                for item in streams
                if item.get("codec_type") == "audio"
            ][:8],
            "has_audio": any(item.get("codec_type") == "audio" for item in streams),
            "size": path.stat().st_size,
            "sha256": _hash_file(path),
        }
    except (KeyError, StopIteration, TypeError, ValueError, ZeroDivisionError) as exc:
        raise JobStoreError("VIDEO_PROBE_INVALID", "FFprobe returned incomplete metadata") from exc
    if (
        result["duration_ms"] <= 0
        or result["width"] <= 0
        or result["height"] <= 0
        or not math.isfinite(result["frame_rate"])
    ):
        raise JobStoreError("VIDEO_PROBE_INVALID", "FFprobe returned invalid metadata")
    return result


class AuditService:
    def __init__(self, store: JobStore, *, max_document_bytes: int | None = None) -> None:
        self.store = store
        self.database = store.database
        self.max_document_bytes = max_document_bytes or _bounded_int(
            "OPENSTORYLINE_AUDIT_MAX_DOCUMENT_BYTES",
            2 * 1024 * 1024,
            1024,
            16 * 1024 * 1024,
        )

    async def ingest_artifact(self, job_id: str, artifact_name: str) -> dict[str, Any]:
        try:
            async with self.database.sessions() as session:
                artifact = await session.scalar(
                    select(Artifact).where(
                        Artifact.job_id == job_id,
                        Artifact.name == artifact_name,
                    )
                )
        except SQLAlchemyError:
            raise JobStoreError("DATABASE_UNAVAILABLE", "audit storage is unavailable") from None
        if artifact is None:
            raise JobStoreError("ARTIFACT_NOT_FOUND", "artifact not found")
        if Path(artifact.name).suffix.lower() not in {".json", ".srt"}:
            return {"created": False, "skipped": True, "source_name": artifact.name}
        return await self._ingest(
            job_id=job_id,
            source_name=artifact.name,
            kind=artifact.kind,
            artifact=artifact,
            path_resolver=lambda: self.store.resolve_artifact_for_audit(
                job_id,
                artifact.name,
            ),
        )

    async def ingest_job_snapshot(self, job_id: str) -> dict[str, Any]:
        await self.store.load_for_audit(job_id)

        async def snapshot_path() -> Path:
            return self.store._state_path(job_id)

        return await self._ingest(
            job_id=job_id,
            source_name="job.json",
            kind="job_state",
            artifact=None,
            path_resolver=snapshot_path,
        )

    async def _ingest(
        self,
        *,
        job_id: str,
        source_name: str,
        kind: str,
        artifact: Artifact | None,
        path_resolver: Callable[[], Awaitable[Path]],
    ) -> dict[str, Any]:
        raw_text = ""
        parsed_data: Any = None
        parse_status = "invalid"
        parse_error_code: str | None = None
        byte_size = int(artifact.size if artifact is not None else 0)
        digest = str(artifact.sha256 or "") if artifact is not None else ""
        try:
            path = await path_resolver()
            byte_size = path.stat().st_size
            if byte_size > self.max_document_bytes:
                parse_error_code = "DOCUMENT_TOO_LARGE"
                digest = digest or _marker_hash(job_id, source_name, parse_error_code)
            else:
                content, oversized = await asyncio.to_thread(
                    _read_bounded,
                    path,
                    self.max_document_bytes,
                )
                digest = _hash_bytes(content)
                if oversized:
                    parse_error_code = "DOCUMENT_TOO_LARGE"
                else:
                    decoded = content.decode("utf-8", errors="replace")
                    if Path(source_name).suffix.lower() == ".json":
                        try:
                            parsed_data = sanitize_audit_document(
                                _parse_json_document(decoded)
                            )
                            if source_name == "repair_report.json":
                                parsed_data = validate_repair_report(parsed_data)
                            raw_text = json.dumps(
                                parsed_data,
                                ensure_ascii=False,
                                separators=(",", ":"),
                                allow_nan=False,
                            )
                            parse_status = "parsed"
                        except RepairContractError:
                            raw_text = ""
                            parsed_data = None
                            parse_error_code = "REPAIR_REPORT_INVALID"
                        except (TypeError, ValueError, json.JSONDecodeError):
                            raw_text = sanitize_text(
                                decoded,
                                limit=max(self.max_document_bytes * 2, len(decoded) + 1),
                            )
                            parse_error_code = "JSON_INVALID"
                    else:
                        raw_text = sanitize_text(
                            decoded,
                            limit=max(self.max_document_bytes * 2, len(decoded) + 1),
                        )
                        parsed_data, parse_status, parse_error_code = _parse_document(
                            source_name,
                            raw_text,
                        )
        except JobStoreError as exc:
            if exc.code == "DATABASE_UNAVAILABLE":
                raise
            parse_error_code = "DOCUMENT_MISSING"
            digest = digest or _marker_hash(job_id, source_name, parse_error_code)
        except OSError:
            parse_error_code = "DOCUMENT_MISSING"
            digest = digest or _marker_hash(job_id, source_name, parse_error_code)
        if len(digest) != 64:
            digest = _marker_hash(job_id, source_name, parse_error_code or "DOCUMENT_INVALID")
        try:
            async with self.database.sessions() as session:
                async with session.begin():
                    job = await session.scalar(
                        select(VideoJob).where(VideoJob.id == job_id).with_for_update()
                    )
                    if job is None:
                        raise JobStoreError("JOB_NOT_FOUND", "job not found")
                    existing = await session.scalar(
                        select(AuditDocument).where(
                            AuditDocument.job_id == job_id,
                            AuditDocument.source_name == source_name,
                            AuditDocument.sha256 == digest,
                        )
                    )
                    if existing is not None:
                        return self._document_state(existing, include_raw=False) | {
                            "created": False
                        }
                    row = AuditDocument(
                        job_id=job_id,
                        artifact_id=artifact.id if artifact is not None else None,
                        kind=sanitize_text(kind, limit=64),
                        source_name=sanitize_text(source_name, limit=255),
                        raw_text=raw_text,
                        parsed_data=parsed_data,
                        parse_status=parse_status,
                        parse_error_code=parse_error_code,
                        parser_version=AUDIT_PARSER_VERSION,
                        sha256=digest,
                        byte_size=max(0, byte_size),
                    )
                    session.add(row)
                    await session.flush()
                    await self.store._append_event(
                        session,
                        job,
                        "audit_document_ingested",
                        {
                            "source_name": source_name,
                            "parse_status": parse_status,
                            "parse_error_code": parse_error_code,
                            "byte_size": max(0, byte_size),
                        },
                    )
        except JobStoreError:
            raise
        except IntegrityError:
            return {"created": False, "source_name": source_name, "sha256": digest}
        except SQLAlchemyError:
            raise JobStoreError("DATABASE_UNAVAILABLE", "audit storage is unavailable") from None
        return self._document_state(row, include_raw=False) | {"created": True}

    async def list_jobs(
        self,
        *,
        since: datetime | None = None,
        editing_session_id: str | None = None,
        state: str | None = None,
        stage: str | None = None,
        verdict: str | None = None,
        error_code: str | None = None,
        media_available: bool | None = None,
        audit_hold: bool | None = None,
        limit: int = 50,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        if not 1 <= int(limit) <= 200:
            raise JobStoreError("PAGE_LIMIT_INVALID", "limit must be between 1 and 200")
        boundary = _decode_cursor(cursor)
        query = (
            select(VideoJob)
            .join(EditingSession, EditingSession.id == VideoJob.editing_session_id)
            .order_by(VideoJob.created_at.desc(), VideoJob.id.desc())
            .limit(int(limit) + 1)
        )
        if since is not None:
            query = query.where(VideoJob.created_at >= since)
        if editing_session_id:
            query = query.where(VideoJob.editing_session_id == editing_session_id)
        if state:
            query = query.where(VideoJob.state == state)
        if stage:
            query = query.where(VideoJob.stage == stage)
        if verdict:
            latest_verdict = (
                select(AuditReview.verdict)
                .where(AuditReview.job_id == VideoJob.id)
                .order_by(AuditReview.created_at.desc(), AuditReview.id.desc())
                .limit(1)
                .scalar_subquery()
            )
            query = query.where(
                latest_verdict == verdict
            )
        if error_code:
            query = query.where(VideoJob.error_data["code"].astext == error_code)
        if media_available is not None:
            media_exists = exists(
                select(Artifact.id).where(
                    Artifact.job_id == VideoJob.id,
                    Artifact.kind == "video",
                    Artifact.availability == "available",
                )
            )
            query = query.where(media_exists if media_available else ~media_exists)
        if audit_hold is not None:
            query = query.where(
                EditingSession.audit_hold_at.is_not(None)
                if audit_hold
                else EditingSession.audit_hold_at.is_(None)
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
                ids = [row.id for row in selected]
                document_counts: dict[str, int] = {}
                reviews: dict[str, AuditReview] = {}
                media: set[str] = set()
                holds: set[str] = set()
                if ids:
                    for job_id, count in (
                        await session.execute(
                            select(AuditDocument.job_id, func.count())
                            .where(AuditDocument.job_id.in_(ids))
                            .group_by(AuditDocument.job_id)
                        )
                    ).all():
                        document_counts[job_id] = int(count)
                    for review in (
                        await session.execute(
                            select(AuditReview)
                            .where(AuditReview.job_id.in_(ids))
                            .distinct(AuditReview.job_id)
                            .order_by(
                                AuditReview.job_id,
                                AuditReview.created_at.desc(),
                                AuditReview.id.desc(),
                            )
                        )
                    ).scalars():
                        reviews[review.job_id] = review
                    media = set(
                        (
                            await session.execute(
                                select(Artifact.job_id).where(
                                    Artifact.job_id.in_(ids),
                                    Artifact.kind == "video",
                                    Artifact.availability == "available",
                                )
                            )
                        ).scalars()
                    )
                    holds = set(
                        (
                            await session.execute(
                                select(EditingSession.id).where(
                                    EditingSession.id.in_(
                                        [row.editing_session_id for row in selected]
                                    ),
                                    EditingSession.audit_hold_at.is_not(None),
                                )
                            )
                        ).scalars()
                    )
        except SQLAlchemyError:
            raise JobStoreError("DATABASE_UNAVAILABLE", "audit queries are unavailable") from None
        has_more = len(rows) > int(limit)
        return {
            "items": [
                {
                    "id": row.id,
                    "editing_session_id": row.editing_session_id,
                    "prompt_version_id": row.prompt_version_id,
                    "attempt_number": row.attempt_number,
                    "is_favorite": row.is_favorite,
                    "favorite_selection_source": "human" if row.is_favorite else None,
                    "source_sha256": (row.input_data or {}).get("sha256"),
                    "settings_version": (row.request_data or {}).get(
                        "settings_version"
                    ),
                    "state": row.state,
                    "stage": row.stage,
                    "error_code": (
                        (row.error_data or {}).get("code")
                        if isinstance(row.error_data, dict)
                        else None
                    ),
                    "media_available": row.id in media,
                    "audit_hold": row.editing_session_id in holds,
                    "document_count": document_counts.get(row.id, 0),
                    "latest_verdict": reviews[row.id].verdict if row.id in reviews else None,
                    "created_at": _iso(row.created_at),
                    "updated_at": _iso(row.updated_at),
                }
                for row in selected
            ],
            "next_cursor": (
                _encode_cursor(selected[-1].created_at, selected[-1].id)
                if has_more and selected
                else None
            ),
        }

    async def outcome_slo_summary(
        self,
        *,
        since: datetime | None = None,
        limit: int = 5000,
    ) -> dict[str, Any]:
        if not 1 <= int(limit) <= 5000:
            raise JobStoreError(
                "PAGE_LIMIT_INVALID",
                "outcome summary limit must be between 1 and 5000",
            )
        query = (
            select(VideoJob)
            .where(
                VideoJob.prompt_version_id.is_not(None),
                VideoJob.state.in_(("completed", "failed")),
                VideoJob.deleted_at.is_(None),
            )
            .order_by(VideoJob.created_at.desc(), VideoJob.id.desc())
            .limit(int(limit) + 1)
        )
        if since is not None:
            query = query.where(VideoJob.created_at >= since)
        try:
            async with self.database.sessions() as session:
                rows = list((await session.execute(query)).scalars())
        except SQLAlchemyError:
            raise JobStoreError(
                "DATABASE_UNAVAILABLE",
                "outcome summary is unavailable",
            ) from None
        selected = rows[: int(limit)]
        summary = build_outcome_slo_summary([
            {
                "outcome": (row.result_data or {}).get("outcome"),
                "retry_of_attempt_id": (row.request_data or {}).get(
                    "retry_of_attempt_id"
                ),
                "prior_limitation_codes": (
                    (row.request_data or {}).get("prior_attempt_quality_feedback") or {}
                ).get("retry_reason_codes", []),
                "started_at": _iso(row.started_at),
                "completed_at": _iso(row.completed_at),
            }
            for row in selected
        ])
        return {
            **summary,
            "rows_scanned": len(selected),
            "since": _iso(since),
            "truncated": len(rows) > int(limit),
        }

    async def defect_records(
        self,
        *,
        since: datetime | None = None,
        code: str | None = None,
        strategy: str | None = None,
        disposition: str | None = None,
        stage: str | None = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        if not 1 <= int(limit) <= 500:
            raise JobStoreError(
                "PAGE_LIMIT_INVALID",
                "defect record limit must be between 1 and 500",
            )
        code_filter = _defect_filter("code", code)
        strategy_filter = _defect_filter(
            "strategy",
            strategy,
            allowed=frozenset(item.value for item in RepairStrategy),
        )
        disposition_filter = _defect_filter(
            "disposition",
            disposition,
            allowed=AUDIT_DEFECT_DISPOSITIONS,
        )
        stage_filter = _defect_filter("stage", stage)
        scan_limit = min(5000, max(250, int(limit) * 20))
        query = (
            select(VideoJob)
            .where(
                VideoJob.state.in_(("completed", "failed")),
                VideoJob.deleted_at.is_(None),
            )
            .order_by(VideoJob.created_at.desc(), VideoJob.id.desc())
            .limit(scan_limit + 1)
        )
        if since is not None:
            query = query.where(VideoJob.created_at >= since)
        try:
            async with self.database.sessions() as session:
                rows = list((await session.execute(query)).scalars())
                selected = rows[:scan_limit]
                job_ids = [row.id for row in selected]
                document_rows = []
                if job_ids:
                    document_rows = list((await session.execute(
                        select(AuditDocument)
                        .where(
                            AuditDocument.job_id.in_(job_ids),
                            AuditDocument.source_name.in_(AUDIT_DEFECT_SOURCES),
                            AuditDocument.parse_status == "parsed",
                        )
                        .order_by(
                            AuditDocument.created_at.desc(),
                            AuditDocument.id.desc(),
                        )
                    )).scalars())
        except SQLAlchemyError:
            raise JobStoreError(
                "DATABASE_UNAVAILABLE",
                "defect audit queries are unavailable",
            ) from None

        documents: dict[str, dict[str, Any]] = {}
        for document in document_rows:
            by_name = documents.setdefault(document.job_id, {})
            if document.source_name not in by_name and isinstance(
                document.parsed_data,
                dict,
            ):
                by_name[document.source_name] = document.parsed_data

        items: list[dict[str, Any]] = []
        records_scanned = 0
        has_more_records = False
        for row in selected:
            job_documents = documents.get(row.id, {})
            result_data = row.result_data if isinstance(row.result_data, dict) else {}
            outcome = result_data.get("outcome") or job_documents.get(
                "outcome_report.json"
            )
            records = _defect_records_from_evidence(
                job_id=row.id,
                attempt_number=row.attempt_number,
                created_at=row.created_at,
                completed_at=row.completed_at,
                outcome=outcome,
                repair_report=job_documents.get("repair_report.json"),
                promotion_report=job_documents.get("render_promotion.json"),
                fallback_ledger=job_documents.get("fallback_ledger.json"),
            )
            for record in records:
                records_scanned += 1
                if code_filter and code_filter not in {
                    record["code"],
                    record["canonical_code"],
                }:
                    continue
                if strategy_filter and record["strategy"] != strategy_filter:
                    continue
                if disposition_filter and record["disposition"] != disposition_filter:
                    continue
                if stage_filter and record["stage"].lower() != stage_filter:
                    continue
                if len(items) >= int(limit):
                    has_more_records = True
                    break
                items.append(record)
            if has_more_records:
                break
        return {
            "kind": "defect_records",
            "items": items,
            "rows_scanned": len(selected),
            "records_scanned": records_scanned,
            "since": _iso(since),
            "truncated": has_more_records or len(rows) > scan_limit,
            "normalization_note": (
                "These bounded records are projected from versioned JSON evidence; "
                "normalize or backfill only after query volume proves it necessary."
            ),
        }

    async def show_job(self, job_id: str, *, limit: int = 200) -> dict[str, Any]:
        if not 1 <= int(limit) <= 500:
            raise JobStoreError("PAGE_LIMIT_INVALID", "show limit is invalid")
        job = await self.store.load_for_audit(job_id)
        try:
            async with self.database.sessions() as session:
                editing_session = await session.get(EditingSession, job["editing_session_id"])
                input_video = await session.scalar(
                    select(SessionInputVideo).where(
                        SessionInputVideo.editing_session_id
                        == job["editing_session_id"]
                    )
                )
                documents = list(
                    (
                        await session.execute(
                            select(AuditDocument)
                            .where(AuditDocument.job_id == job_id)
                            .order_by(AuditDocument.created_at, AuditDocument.id)
                            .limit(int(limit) + 1)
                        )
                    ).scalars()
                )
                reviews = list(
                    (
                        await session.execute(
                            select(AuditReview)
                            .where(AuditReview.job_id == job_id)
                            .order_by(AuditReview.created_at, AuditReview.id)
                            .limit(int(limit) + 1)
                        )
                    ).scalars()
                )
        except SQLAlchemyError:
            raise JobStoreError("DATABASE_UNAVAILABLE", "audit queries are unavailable") from None
        return {
            "job": job,
            "editing_session": (
                {
                    "id": editing_session.id,
                    "title": editing_session.title,
                    "workflow_version": editing_session.workflow_version,
                    "input_video": (
                        {
                            "id": input_video.id,
                            "state": input_video.state,
                            "sha256": input_video.sha256,
                            "expires_at": _iso(input_video.expires_at),
                            "purged_at": _iso(input_video.purged_at),
                        }
                        if input_video is not None
                        else None
                    ),
                    "deleted_at": _iso(editing_session.deleted_at),
                    "audit_expires_at": _iso(editing_session.audit_expires_at),
                    "audit_hold_at": _iso(editing_session.audit_hold_at),
                    "audit_hold_reason": editing_session.audit_hold_reason,
                }
                if editing_session is not None
                else None
            ),
            "documents": [
                self._document_state(row, include_raw=False)
                for row in documents[: int(limit)]
            ],
            "reviews": [self._review_state(row) for row in reviews[: int(limit)]],
            "truncated": {
                "documents": len(documents) > int(limit),
                "reviews": len(reviews) > int(limit),
            },
        }

    async def documents(self, job_id: str, *, limit: int = 200) -> list[dict[str, Any]]:
        if not 1 <= int(limit) <= 500:
            raise JobStoreError("PAGE_LIMIT_INVALID", "document limit is invalid")
        await self.store.load_for_audit(job_id)
        try:
            async with self.database.sessions() as session:
                rows = list(
                    (
                        await session.execute(
                            select(AuditDocument)
                            .where(AuditDocument.job_id == job_id)
                            .order_by(AuditDocument.created_at, AuditDocument.id)
                            .limit(int(limit))
                        )
                    ).scalars()
                )
        except SQLAlchemyError:
            raise JobStoreError("DATABASE_UNAVAILABLE", "audit documents are unavailable") from None
        return [self._document_state(row, include_raw=True) for row in rows]

    async def verify_job(self, job_id: str) -> dict[str, Any]:
        job = await self.store.load_for_audit(job_id)
        try:
            async with self.database.sessions() as session:
                manifest = await session.scalar(
                    select(AuditDocument)
                    .where(
                        AuditDocument.job_id == job_id,
                        AuditDocument.source_name == "manifest.json",
                        AuditDocument.parse_status == "parsed",
                    )
                    .order_by(AuditDocument.created_at.desc(), AuditDocument.id.desc())
                    .limit(1)
                )
                subtitle_documents = list(
                    (
                        await session.execute(
                            select(AuditDocument)
                            .where(
                                AuditDocument.job_id == job_id,
                                AuditDocument.source_name.like("%.srt"),
                            )
                            .order_by(AuditDocument.created_at.desc(), AuditDocument.id.desc())
                        )
                    ).scalars()
                )
                promotion_document = await session.scalar(
                    select(AuditDocument)
                    .where(
                        AuditDocument.job_id == job_id,
                        AuditDocument.source_name == "render_promotion.json",
                    )
                    .order_by(AuditDocument.created_at.desc(), AuditDocument.id.desc())
                    .limit(1)
                )
                frame_quality_document = await session.scalar(
                    select(AuditDocument)
                    .where(
                        AuditDocument.job_id == job_id,
                        AuditDocument.source_name == "frame_quality_qa.json",
                    )
                    .order_by(AuditDocument.created_at.desc(), AuditDocument.id.desc())
                    .limit(1)
                )
        except SQLAlchemyError:
            raise JobStoreError("DATABASE_UNAVAILABLE", "audit verification is unavailable") from None
        checks: list[dict[str, Any]] = []
        manifest_data = manifest.parsed_data if manifest is not None else None
        if not isinstance(manifest_data, dict):
            checks.append({"code": "MANIFEST_MISSING", "status": "fail"})
            return await self._store_system_review(job, "rejected", checks)
        plan = manifest_data.get("plan") or {}
        agentic = manifest_data.get("agentic") or {}
        promotion_reference = (
            agentic.get("render_promotion")
            if isinstance(agentic, dict)
            else None
        )
        if isinstance(promotion_reference, dict) and promotion_reference.get("artifact"):
            promotion = (
                promotion_document.parsed_data
                if promotion_document is not None
                and promotion_document.parse_status == "parsed"
                else None
            )
            frame_quality = (
                frame_quality_document.parsed_data
                if frame_quality_document is not None
                and frame_quality_document.parse_status == "parsed"
                else None
            )
            mode = str((promotion or {}).get("mode") or "")
            decision = str((promotion or {}).get("decision") or "")
            promotion_ok = (
                isinstance(promotion, dict)
                and mode in {"off", "report", "enforce"}
                and decision in {"off", "observe", "promote"}
                and not (mode == "enforce" and decision != "promote")
            )
            checks.append({
                "code": "RENDER_PROMOTION",
                "status": "pass" if promotion_ok else "fail",
                "mode": mode,
                "decision": decision,
            })
            checks.append({
                "code": "FRAME_QUALITY_EVIDENCE",
                "status": "pass" if isinstance(frame_quality, dict) else "fail",
                "quality_status": (
                    str(frame_quality.get("status") or "")
                    if isinstance(frame_quality, dict)
                    else ""
                ),
            })
        clips = list(plan.get("clips") or []) if isinstance(plan, dict) else []
        outputs = list(manifest_data.get("outputs") or [])
        videos = {
            item.get("name"): item
            for item in job.get("artifacts", [])
            if item.get("kind") == "video"
        }
        count_ok = len(outputs) == len(clips) == len(videos) and len(outputs) > 0
        checks.append(
            {
                "code": "OUTPUT_COUNT",
                "status": "pass" if count_ok else "fail",
                "planned": len(clips),
                "manifest_outputs": len(outputs),
                "registered_videos": len(videos),
            }
        )
        subtitles: dict[str, AuditDocument] = {}
        for row in subtitle_documents:
            subtitles.setdefault(row.source_name, row)
        for index, output in enumerate(outputs[:50]):
            if not isinstance(output, dict):
                checks.append({"code": "OUTPUT_ENTRY_INVALID", "status": "fail", "index": index})
                continue
            video_name = str(output.get("video") or "")
            clip = output.get("clip") if isinstance(output.get("clip"), dict) else {}
            try:
                expected_duration = int(
                    clip.get("duration_ms")
                    or (int(clip.get("end_ms") or 0) - int(clip.get("start_ms") or 0))
                )
            except (TypeError, ValueError):
                checks.append(
                    {"code": "OUTPUT_CLIP_INVALID", "status": "fail", "output": video_name}
                )
                continue
            if video_name not in videos:
                checks.append(
                    {"code": "VIDEO_ARTIFACT_MISSING", "status": "fail", "output": video_name}
                )
                continue
            try:
                path = await self.store.resolve_artifact_for_audit(job_id, video_name)
                measured = await asyncio.to_thread(_probe_video, path)
            except JobStoreError as exc:
                checks.append(
                    {"code": exc.code, "status": "fail", "output": video_name}
                )
                continue
            tolerance = max(750, int(expected_duration * 0.05))
            duration_ok = expected_duration > 0 and abs(
                measured["duration_ms"] - expected_duration
            ) <= tolerance
            checks.append(
                {
                    "code": "VIDEO_STRUCTURE",
                    "status": "pass" if duration_ok else "fail",
                    "output": video_name,
                    "expected_duration_ms": expected_duration,
                    "duration_tolerance_ms": tolerance,
                    **measured,
                }
            )
            subtitle_name = str(output.get("subtitles") or "")
            if subtitle_name:
                subtitle = subtitles.get(subtitle_name)
                summary = subtitle.parsed_data if subtitle is not None else None
                subtitle_ok = (
                    subtitle is not None
                    and subtitle.parse_status == "parsed"
                    and isinstance(summary, dict)
                    and int(summary.get("ordering_errors") or 0) == 0
                    and int(summary.get("invalid_cues") or 0) == 0
                    and int(summary.get("last_end_ms") or 0) <= measured["duration_ms"] + 250
                )
                checks.append(
                    {
                        "code": "SUBTITLE_STRUCTURE",
                        "status": "pass" if subtitle_ok else "fail",
                        "output": subtitle_name,
                        "summary": summary if isinstance(summary, dict) else None,
                    }
                )
        verdict = "approved" if checks and all(item["status"] == "pass" for item in checks) else "rejected"
        return await self._store_system_review(job, verdict, checks)

    async def _store_system_review(
        self,
        job: dict[str, Any],
        verdict: str,
        checks: list[dict[str, Any]],
    ) -> dict[str, Any]:
        findings = {
            "scope": "deterministic_structural",
            "creative_quality_evaluated": False,
            "checks": sanitize_for_persistence(checks),
            "passed": sum(item.get("status") == "pass" for item in checks),
            "failed": sum(item.get("status") == "fail" for item in checks),
        }
        return await self.add_review(
            job["id"],
            verdict=verdict,
            source="system",
            reviewer_label="deterministic-qc-v1",
            notes=None,
            findings=findings,
        )

    async def add_review(
        self,
        job_id: str,
        *,
        verdict: str,
        source: str,
        reviewer_label: str | None,
        notes: str | None,
        findings: Any,
    ) -> dict[str, Any]:
        if verdict not in {"approved", "rejected", "needs_review"}:
            raise JobStoreError("AUDIT_REVIEW_INVALID", "review verdict is invalid")
        if source not in {"system", "agent", "human"}:
            raise JobStoreError("AUDIT_REVIEW_INVALID", "review source is invalid")
        clean_notes = sanitize_text(notes, limit=20_000).strip() if notes else None
        clean_findings = sanitize_for_persistence(findings or {})
        if not isinstance(clean_findings, dict):
            raise JobStoreError("AUDIT_REVIEW_INVALID", "review findings must be an object")
        try:
            async with self.database.sessions() as session:
                async with session.begin():
                    job = await session.scalar(
                        select(VideoJob).where(VideoJob.id == job_id).with_for_update()
                    )
                    if job is None:
                        raise JobStoreError("JOB_NOT_FOUND", "job not found")
                    row = AuditReview(
                        job_id=job_id,
                        verdict=verdict,
                        source=source,
                        reviewer_label=(
                            sanitize_text(reviewer_label, limit=120).strip()
                            if reviewer_label
                            else None
                        ),
                        notes=clean_notes,
                        findings=clean_findings,
                    )
                    session.add(row)
                    await session.flush()
                    await self.store._append_event(
                        session,
                        job,
                        "audit_review_recorded",
                        {"review_id": row.id, "verdict": verdict, "source": source},
                    )
        except JobStoreError:
            raise
        except SQLAlchemyError:
            raise JobStoreError("DATABASE_UNAVAILABLE", "audit review storage is unavailable") from None
        return self._review_state(row)

    async def backfill(self, *, dry_run: bool, limit: int = 100) -> dict[str, int]:
        if not 1 <= int(limit) <= 1000:
            raise JobStoreError("AUDIT_BATCH_INVALID", "limit must be between 1 and 1000")
        try:
            async with self.database.sessions() as session:
                snapshot_jobs = list(
                    (
                        await session.execute(
                            select(VideoJob)
                            .where(
                                ~exists(
                                    select(AuditDocument.id).where(
                                        AuditDocument.job_id == VideoJob.id,
                                        AuditDocument.source_name == "job.json",
                                    )
                                )
                            )
                            .order_by(VideoJob.id)
                            .limit(int(limit))
                        )
                    ).scalars()
                )
                remaining = max(0, int(limit) - len(snapshot_jobs))
                artifacts = list(
                    (
                        await session.execute(
                            select(Artifact)
                            .where(
                                or_(
                                    Artifact.name.like("%.json"),
                                    Artifact.name.like("%.srt"),
                                ),
                                or_(
                                    and_(
                                        Artifact.sha256.is_not(None),
                                        ~exists(
                                            select(AuditDocument.id).where(
                                                AuditDocument.artifact_id == Artifact.id,
                                                AuditDocument.sha256 == Artifact.sha256,
                                            )
                                        ),
                                    ),
                                    and_(
                                        Artifact.sha256.is_(None),
                                        ~exists(
                                            select(AuditDocument.id).where(
                                                AuditDocument.artifact_id == Artifact.id
                                            )
                                        ),
                                    ),
                                ),
                            )
                            .order_by(Artifact.id)
                            .limit(remaining)
                        )
                    ).scalars()
                ) if remaining else []
        except SQLAlchemyError:
            raise JobStoreError("DATABASE_UNAVAILABLE", "audit backfill is unavailable") from None
        report = {
            "scanned": len(snapshot_jobs) + len(artifacts),
            "would_ingest": 0,
            "ingested": 0,
            "existing": 0,
            "invalid": 0,
        }
        for job in snapshot_jobs:
            if dry_run:
                report["would_ingest"] += 1
                continue
            result = await self.ingest_job_snapshot(job.id)
            report["ingested" if result.get("created") else "existing"] += 1
            if result.get("parse_status") == "invalid":
                report["invalid"] += 1
        for artifact in artifacts:
            digest = artifact.sha256
            exists_now = False
            if digest:
                try:
                    async with self.database.sessions() as session:
                        exists_now = bool(
                            await session.scalar(
                                select(AuditDocument.id).where(
                                    AuditDocument.artifact_id == artifact.id,
                                    AuditDocument.sha256 == digest,
                                )
                            )
                        )
                except SQLAlchemyError:
                    raise JobStoreError("DATABASE_UNAVAILABLE", "audit backfill is unavailable") from None
            if exists_now:
                report["existing"] += 1
                continue
            if dry_run:
                report["would_ingest"] += 1
                continue
            result = await self.ingest_artifact(artifact.job_id, artifact.name)
            report["ingested" if result.get("created") else "existing"] += 1
            if result.get("parse_status") == "invalid":
                report["invalid"] += 1
        return report

    @staticmethod
    def _document_state(row: AuditDocument, *, include_raw: bool) -> dict[str, Any]:
        value = {
            "id": row.id,
            "job_id": row.job_id,
            "artifact_id": row.artifact_id,
            "kind": row.kind,
            "source_name": row.source_name,
            "parsed_data": row.parsed_data,
            "parse_status": row.parse_status,
            "parse_error_code": row.parse_error_code,
            "parser_version": row.parser_version,
            "sha256": row.sha256,
            "byte_size": row.byte_size,
            "created_at": _iso(row.created_at),
        }
        if include_raw:
            value["raw_text"] = row.raw_text
        return value

    @staticmethod
    def _review_state(row: AuditReview) -> dict[str, Any]:
        return {
            "id": row.id,
            "job_id": row.job_id,
            "verdict": row.verdict,
            "source": row.source,
            "reviewer_label": row.reviewer_label,
            "notes": row.notes,
            "findings": row.findings,
            "created_at": _iso(row.created_at),
        }
