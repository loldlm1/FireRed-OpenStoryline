from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
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
    workflow_version: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="1"
    )
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
        CheckConstraint(
            "workflow_version IN (1, 2)",
            name="ck_editing_sessions_workflow_version",
        ),
        Index("ix_editing_sessions_active_updated", "deleted_at", "updated_at"),
        Index("ix_editing_sessions_audit_expiry", "audit_expires_at"),
    )


class SessionInputVideo(Base):
    __tablename__ = "session_input_videos"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    editing_session_id: Mapped[str] = mapped_column(
        ForeignKey("editing_sessions.id", ondelete="RESTRICT"), nullable=False
    )
    state: Mapped[str] = mapped_column(String(16), nullable=False)
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    expected_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    received_bytes: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default="0"
    )
    media_type: Mapped[str | None] = mapped_column(String(255))
    relative_path: Mapped[str | None] = mapped_column(String(1024))
    sha256: Mapped[str | None] = mapped_column(String(64))
    failure_code: Mapped[str | None] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    purged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint(
            "editing_session_id", name="uq_session_input_videos_session"
        ),
        CheckConstraint("id ~ '^[a-f0-9]{32}$'", name="ck_session_input_videos_id"),
        CheckConstraint(
            "state IN ('pending', 'uploading', 'validating', 'ready', 'failed', 'expired', 'deleted')",
            name="ck_session_input_videos_state",
        ),
        CheckConstraint(
            "length(btrim(original_filename)) > 0",
            name="ck_session_input_videos_filename_present",
        ),
        CheckConstraint(
            "expected_size > 0", name="ck_session_input_videos_expected_size_positive"
        ),
        CheckConstraint(
            "received_bytes >= 0 AND received_bytes <= expected_size",
            name="ck_session_input_videos_received_bytes",
        ),
        CheckConstraint(
            "sha256 IS NULL OR length(sha256) = 64",
            name="ck_session_input_videos_sha256_length",
        ),
        CheckConstraint(
            "state <> 'ready' OR (received_bytes = expected_size AND relative_path IS NOT NULL AND length(btrim(relative_path)) > 0 AND media_type IS NOT NULL AND length(btrim(media_type)) > 0 AND sha256 IS NOT NULL AND length(sha256) = 64 AND completed_at IS NOT NULL AND expires_at IS NOT NULL)",
            name="ck_session_input_videos_ready_metadata",
        ),
        CheckConstraint(
            "relative_path IS NULL OR state IN ('ready', 'expired', 'deleted')",
            name="ck_session_input_videos_path_state",
        ),
        Index("ix_session_input_videos_state_expiry", "state", "expires_at"),
        Index("ix_session_input_videos_updated", "updated_at"),
    )


class PromptVersion(Base):
    __tablename__ = "prompt_versions"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    editing_session_id: Mapped[str] = mapped_column(
        ForeignKey("editing_sessions.id", ondelete="RESTRICT"), nullable=False
    )
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    settings_data: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        UniqueConstraint(
            "editing_session_id",
            "version_number",
            name="uq_prompt_versions_session_number",
        ),
        CheckConstraint("id ~ '^[a-f0-9]{32}$'", name="ck_prompt_versions_id"),
        CheckConstraint(
            "version_number >= 1", name="ck_prompt_versions_number_positive"
        ),
        CheckConstraint(
            "length(btrim(prompt)) > 0 AND length(prompt) <= 12000",
            name="ck_prompt_versions_prompt",
        ),
        Index(
            "ix_prompt_versions_session_created",
            "editing_session_id",
            "created_at",
            "id",
        ),
    )


class VideoJob(Base):
    __tablename__ = "video_jobs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    editing_session_id: Mapped[str] = mapped_column(
        ForeignKey("editing_sessions.id", ondelete="RESTRICT"), nullable=False
    )
    prompt_version_id: Mapped[str | None] = mapped_column(
        ForeignKey("prompt_versions.id", ondelete="RESTRICT")
    )
    attempt_number: Mapped[int | None] = mapped_column(Integer)
    is_favorite: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
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
        CheckConstraint(
            "attempt_number IS NULL OR attempt_number >= 1",
            name="ck_video_jobs_attempt_positive",
        ),
        CheckConstraint(
            "NOT is_favorite OR (prompt_version_id IS NOT NULL AND attempt_number IS NOT NULL)",
            name="ck_video_jobs_favorite_versioned",
        ),
        UniqueConstraint(
            "prompt_version_id",
            "attempt_number",
            name="uq_video_jobs_prompt_attempt",
        ),
        Index("ix_video_jobs_session_created", "editing_session_id", "created_at"),
        Index("ix_video_jobs_state_created", "state", "created_at"),
        Index("ix_video_jobs_media_expiry", "media_expires_at"),
        Index("ix_video_jobs_audit_expiry", "audit_expires_at"),
        Index(
            "uq_video_jobs_session_favorite",
            "editing_session_id",
            unique=True,
            postgresql_where=text("is_favorite AND deleted_at IS NULL"),
        ),
    )


class SessionAnalysisCache(Base):
    __tablename__ = "session_analysis_cache"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    editing_session_id: Mapped[str] = mapped_column(
        ForeignKey("editing_sessions.id", ondelete="CASCADE"), nullable=False
    )
    input_video_id: Mapped[str] = mapped_column(
        ForeignKey("session_input_videos.id", ondelete="CASCADE"), nullable=False
    )
    stage: Mapped[str] = mapped_column(String(64), nullable=False)
    contract_version: Mapped[str] = mapped_column(String(64), nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    relative_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    byte_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    metadata_data: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "input_video_id",
            "stage",
            "fingerprint",
            name="uq_session_analysis_cache_fingerprint",
        ),
        CheckConstraint(
            "stage ~ '^[a-z0-9_]{1,64}$'",
            name="ck_session_analysis_cache_stage",
        ),
        CheckConstraint(
            "length(fingerprint) = 64 AND length(sha256) = 64",
            name="ck_session_analysis_cache_hashes",
        ),
        CheckConstraint(
            "byte_size >= 0", name="ck_session_analysis_cache_size_nonnegative"
        ),
        CheckConstraint(
            "status IN ('available', 'quarantined')",
            name="ck_session_analysis_cache_status",
        ),
        Index(
            "ix_session_analysis_cache_lookup",
            "editing_session_id",
            "input_video_id",
            "stage",
            "fingerprint",
        ),
        Index("ix_session_analysis_cache_expiry", "expires_at"),
    )


class JobStageCheckpoint(Base):
    __tablename__ = "job_stage_checkpoints"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(
        ForeignKey("video_jobs.id", ondelete="CASCADE"), nullable=False
    )
    stage: Mapped[str] = mapped_column(String(64), nullable=False)
    contract_version: Mapped[str] = mapped_column(String(64), nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    relative_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    byte_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    reused_from_job_id: Mapped[str | None] = mapped_column(
        ForeignKey("video_jobs.id", ondelete="SET NULL")
    )
    metadata_data: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "job_id", "stage", "fingerprint", name="uq_job_stage_checkpoint_fingerprint"
        ),
        CheckConstraint(
            "stage ~ '^[a-z0-9_]{1,64}$'", name="ck_job_stage_checkpoint_stage"
        ),
        CheckConstraint(
            "length(fingerprint) = 64 AND length(sha256) = 64",
            name="ck_job_stage_checkpoint_hashes",
        ),
        CheckConstraint(
            "byte_size >= 0", name="ck_job_stage_checkpoint_size_nonnegative"
        ),
        CheckConstraint(
            "status IN ('available', 'quarantined')",
            name="ck_job_stage_checkpoint_status",
        ),
        Index("ix_job_stage_checkpoint_lookup", "job_id", "stage", "fingerprint"),
        Index("ix_job_stage_checkpoint_expiry", "expires_at"),
    )


class JobEvent(Base):
    __tablename__ = "job_events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(
        ForeignKey("video_jobs.id", ondelete="CASCADE"), nullable=False
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    event_type: Mapped[str] = mapped_column(String(80), nullable=False)
    audience: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="internal"
    )
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
        CheckConstraint(
            "audience IN ('internal', 'user')", name="ck_job_events_audience"
        ),
        Index("ix_job_events_job_time", "job_id", "occurred_at"),
        Index(
            "ix_job_events_job_audience_sequence",
            "job_id",
            "audience",
            "sequence",
        ),
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
