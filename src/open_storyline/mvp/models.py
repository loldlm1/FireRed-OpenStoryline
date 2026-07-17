from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class AuthSession(Base):
    __tablename__ = "auth_sessions"

    token_digest: Mapped[str] = mapped_column(String(64), primary_key=True)
    csrf_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    client_digest: Mapped[str | None] = mapped_column(String(64))
    user_agent_digest: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    idle_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    absolute_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint("length(token_digest) = 64", name="ck_auth_sessions_token_digest"),
        CheckConstraint("length(csrf_digest) = 64", name="ck_auth_sessions_csrf_digest"),
        CheckConstraint(
            "idle_expires_at <= absolute_expires_at",
            name="ck_auth_sessions_idle_before_absolute",
        ),
        Index("ix_auth_sessions_expiry", "absolute_expires_at", "idle_expires_at"),
    )


class LoginAttemptBucket(Base):
    __tablename__ = "login_attempt_buckets"

    scope_digest: Mapped[str] = mapped_column(String(64), primary_key=True)
    window_kind: Mapped[str] = mapped_column(String(10), primary_key=True)
    bucket: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    hits: Mapped[int] = mapped_column(Integer, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        CheckConstraint(
            "scope_digest = 'global' OR length(scope_digest) = 64",
            name="ck_login_bucket_scope_digest",
        ),
        CheckConstraint("window_kind IN ('minute', 'day')", name="ck_login_bucket_window"),
        CheckConstraint("hits >= 0", name="ck_login_bucket_hits_nonnegative"),
        Index("ix_login_attempt_buckets_updated", "updated_at"),
    )


class EditingSession(Base):
    __tablename__ = "editing_sessions"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    title: Mapped[str] = mapped_column(String(160), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    audit_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    audit_hold_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    audit_hold_reason: Mapped[str | None] = mapped_column(String(500))

    __table_args__ = (
        CheckConstraint("id ~ '^[a-f0-9]{32}$'", name="ck_editing_sessions_id"),
        CheckConstraint("length(btrim(title)) > 0", name="ck_editing_sessions_title_present"),
        Index("ix_editing_sessions_active_updated", "deleted_at", "updated_at"),
        Index("ix_editing_sessions_audit_expiry", "audit_expires_at"),
    )


class VideoJob(Base):
    __tablename__ = "video_jobs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    editing_session_id: Mapped[str] = mapped_column(
        ForeignKey("editing_sessions.id", ondelete="RESTRICT"), nullable=False
    )
    state: Mapped[str] = mapped_column(String(24), nullable=False)
    stage: Mapped[str | None] = mapped_column(String(64))
    progress: Mapped[Decimal] = mapped_column(Numeric(5, 4), nullable=False)
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    request_data: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    input_data: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    error_data: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    result_data: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    recovery_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    media_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    audit_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        CheckConstraint("id ~ '^[a-f0-9]{32}$'", name="ck_video_jobs_id"),
        CheckConstraint(
            "state IN ('uploading', 'queued', 'running', 'completed', 'failed', 'cancelled')",
            name="ck_video_jobs_state",
        ),
        CheckConstraint("progress >= 0 AND progress <= 1", name="ck_video_jobs_progress"),
        CheckConstraint("length(prompt) <= 12000", name="ck_video_jobs_prompt_length"),
        CheckConstraint("recovery_count >= 0", name="ck_video_jobs_recovery_nonnegative"),
        CheckConstraint("version >= 1", name="ck_video_jobs_version_positive"),
        Index("ix_video_jobs_session_created", "editing_session_id", "created_at"),
        Index("ix_video_jobs_state_created", "state", "created_at"),
        Index("ix_video_jobs_media_expiry", "media_expires_at"),
        Index("ix_video_jobs_audit_expiry", "audit_expires_at"),
    )


class JobEvent(Base):
    __tablename__ = "job_events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(
        ForeignKey("video_jobs.id", ondelete="CASCADE"), nullable=False
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    event_type: Mapped[str] = mapped_column(String(80), nullable=False)
    state: Mapped[str | None] = mapped_column(String(24))
    stage: Mapped[str | None] = mapped_column(String(64))
    payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        UniqueConstraint("job_id", "sequence", name="uq_job_events_job_sequence"),
        CheckConstraint("sequence >= 1", name="ck_job_events_sequence_positive"),
        Index("ix_job_events_job_time", "job_id", "occurred_at"),
        Index("ix_job_events_type_time", "event_type", "occurred_at"),
    )


class Artifact(Base):
    __tablename__ = "artifacts"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(
        ForeignKey("video_jobs.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    kind: Mapped[str] = mapped_column(String(64), nullable=False)
    relative_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    mime_type: Mapped[str | None] = mapped_column(String(255))
    size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sha256: Mapped[str | None] = mapped_column(String(64))
    availability: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    retention_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    purged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    purge_reason: Mapped[str | None] = mapped_column(String(80))

    __table_args__ = (
        UniqueConstraint("job_id", "name", name="uq_artifacts_job_name"),
        CheckConstraint("size >= 0", name="ck_artifacts_size_nonnegative"),
        CheckConstraint(
            "sha256 IS NULL OR length(sha256) = 64",
            name="ck_artifacts_sha256_length",
        ),
        CheckConstraint(
            "availability IN ('available', 'deleted', 'missing')",
            name="ck_artifacts_availability",
        ),
        Index("ix_artifacts_job_kind", "job_id", "kind"),
        Index("ix_artifacts_retention", "retention_expires_at"),
    )


class AuditDocument(Base):
    __tablename__ = "audit_documents"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(
        ForeignKey("video_jobs.id", ondelete="CASCADE"), nullable=False
    )
    artifact_id: Mapped[int | None] = mapped_column(
        ForeignKey("artifacts.id", ondelete="SET NULL")
    )
    kind: Mapped[str] = mapped_column(String(64), nullable=False)
    source_name: Mapped[str] = mapped_column(String(255), nullable=False)
    raw_text: Mapped[str] = mapped_column(Text, nullable=False)
    parsed_data: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    parse_status: Mapped[str] = mapped_column(String(16), nullable=False)
    parse_error_code: Mapped[str | None] = mapped_column(String(160))
    parser_version: Mapped[str] = mapped_column(String(32), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    byte_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        UniqueConstraint("artifact_id", "sha256", name="uq_audit_documents_artifact_hash"),
        UniqueConstraint(
            "job_id", "source_name", "sha256", name="uq_audit_documents_job_source_hash"
        ),
        CheckConstraint("parse_status IN ('parsed', 'invalid')", name="ck_audit_documents_parse"),
        CheckConstraint("length(sha256) = 64", name="ck_audit_documents_sha256_length"),
        CheckConstraint("byte_size >= 0", name="ck_audit_documents_size_nonnegative"),
        Index("ix_audit_documents_job_kind", "job_id", "kind"),
        Index("ix_audit_documents_created", "created_at"),
    )


class AuditReview(Base):
    __tablename__ = "audit_reviews"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(
        ForeignKey("video_jobs.id", ondelete="CASCADE"), nullable=False
    )
    verdict: Mapped[str] = mapped_column(String(24), nullable=False)
    source: Mapped[str] = mapped_column(String(12), nullable=False)
    reviewer_label: Mapped[str | None] = mapped_column(String(120))
    notes: Mapped[str | None] = mapped_column(Text)
    findings: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        CheckConstraint(
            "verdict IN ('approved', 'rejected', 'needs_review')",
            name="ck_audit_reviews_verdict",
        ),
        CheckConstraint(
            "source IN ('system', 'agent', 'human')",
            name="ck_audit_reviews_source",
        ),
        Index("ix_audit_reviews_job_created", "job_id", "created_at"),
        Index("ix_audit_reviews_verdict_created", "verdict", "created_at"),
    )
