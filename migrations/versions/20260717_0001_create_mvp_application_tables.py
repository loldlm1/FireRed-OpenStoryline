"""Create remote MVP application tables.

Revision ID: 20260717_0001
Revises:
Create Date: 2026-07-17
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260717_0001"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "auth_sessions",
        sa.Column("token_digest", sa.String(64), primary_key=True),
        sa.Column("csrf_digest", sa.String(64), nullable=False),
        sa.Column("client_digest", sa.String(64)),
        sa.Column("user_agent_digest", sa.String(64)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("idle_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("absolute_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("length(token_digest) = 64", name="ck_auth_sessions_token_digest"),
        sa.CheckConstraint("length(csrf_digest) = 64", name="ck_auth_sessions_csrf_digest"),
        sa.CheckConstraint(
            "idle_expires_at <= absolute_expires_at",
            name="ck_auth_sessions_idle_before_absolute",
        ),
    )
    op.create_index(
        "ix_auth_sessions_expiry",
        "auth_sessions",
        ["absolute_expires_at", "idle_expires_at"],
    )

    op.create_table(
        "login_attempt_buckets",
        sa.Column("scope_digest", sa.String(64), primary_key=True),
        sa.Column("window_kind", sa.String(10), primary_key=True),
        sa.Column("bucket", sa.BigInteger(), primary_key=True),
        sa.Column("hits", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "scope_digest = 'global' OR length(scope_digest) = 64",
            name="ck_login_bucket_scope_digest",
        ),
        sa.CheckConstraint("window_kind IN ('minute', 'day')", name="ck_login_bucket_window"),
        sa.CheckConstraint("hits >= 0", name="ck_login_bucket_hits_nonnegative"),
    )
    op.create_index(
        "ix_login_attempt_buckets_updated",
        "login_attempt_buckets",
        ["updated_at"],
    )

    op.create_table(
        "editing_sessions",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("title", sa.String(160), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
        sa.Column("audit_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("audit_hold_at", sa.DateTime(timezone=True)),
        sa.Column("audit_hold_reason", sa.String(500)),
        sa.CheckConstraint("id ~ '^[a-f0-9]{32}$'", name="ck_editing_sessions_id"),
        sa.CheckConstraint("length(btrim(title)) > 0", name="ck_editing_sessions_title_present"),
    )
    op.create_index(
        "ix_editing_sessions_active_updated",
        "editing_sessions",
        ["deleted_at", "updated_at"],
    )
    op.create_index(
        "ix_editing_sessions_audit_expiry",
        "editing_sessions",
        ["audit_expires_at"],
    )

    op.create_table(
        "video_jobs",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column(
            "editing_session_id",
            sa.String(32),
            sa.ForeignKey("editing_sessions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("state", sa.String(24), nullable=False),
        sa.Column("stage", sa.String(64)),
        sa.Column("progress", sa.Numeric(5, 4), nullable=False),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column("request_data", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("input_data", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("error_data", postgresql.JSONB(astext_type=sa.Text())),
        sa.Column("result_data", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("recovery_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
        sa.Column("media_expires_at", sa.DateTime(timezone=True)),
        sa.Column("audit_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("id ~ '^[a-f0-9]{32}$'", name="ck_video_jobs_id"),
        sa.CheckConstraint(
            "state IN ('uploading', 'queued', 'running', 'completed', 'failed', 'cancelled')",
            name="ck_video_jobs_state",
        ),
        sa.CheckConstraint("progress >= 0 AND progress <= 1", name="ck_video_jobs_progress"),
        sa.CheckConstraint("length(prompt) <= 12000", name="ck_video_jobs_prompt_length"),
        sa.CheckConstraint("recovery_count >= 0", name="ck_video_jobs_recovery_nonnegative"),
        sa.CheckConstraint("version >= 1", name="ck_video_jobs_version_positive"),
    )
    op.create_index("ix_video_jobs_session_created", "video_jobs", ["editing_session_id", "created_at"])
    op.create_index("ix_video_jobs_state_created", "video_jobs", ["state", "created_at"])
    op.create_index("ix_video_jobs_media_expiry", "video_jobs", ["media_expires_at"])
    op.create_index("ix_video_jobs_audit_expiry", "video_jobs", ["audit_expires_at"])

    op.create_table(
        "job_events",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("job_id", sa.String(32), sa.ForeignKey("video_jobs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(80), nullable=False),
        sa.Column("state", sa.String(24)),
        sa.Column("stage", sa.String(64)),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("job_id", "sequence", name="uq_job_events_job_sequence"),
        sa.CheckConstraint("sequence >= 1", name="ck_job_events_sequence_positive"),
    )
    op.create_index("ix_job_events_job_time", "job_events", ["job_id", "occurred_at"])
    op.create_index("ix_job_events_type_time", "job_events", ["event_type", "occurred_at"])

    op.create_table(
        "artifacts",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("job_id", sa.String(32), sa.ForeignKey("video_jobs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("kind", sa.String(64), nullable=False),
        sa.Column("relative_path", sa.String(1024), nullable=False),
        sa.Column("mime_type", sa.String(255)),
        sa.Column("size", sa.BigInteger(), nullable=False),
        sa.Column("sha256", sa.String(64)),
        sa.Column("availability", sa.String(16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("retention_expires_at", sa.DateTime(timezone=True)),
        sa.Column("purged_at", sa.DateTime(timezone=True)),
        sa.Column("purge_reason", sa.String(80)),
        sa.UniqueConstraint("job_id", "name", name="uq_artifacts_job_name"),
        sa.CheckConstraint("size >= 0", name="ck_artifacts_size_nonnegative"),
        sa.CheckConstraint(
            "sha256 IS NULL OR length(sha256) = 64",
            name="ck_artifacts_sha256_length",
        ),
        sa.CheckConstraint(
            "availability IN ('available', 'deleted', 'missing')",
            name="ck_artifacts_availability",
        ),
    )
    op.create_index("ix_artifacts_job_kind", "artifacts", ["job_id", "kind"])
    op.create_index("ix_artifacts_retention", "artifacts", ["retention_expires_at"])

    op.create_table(
        "audit_documents",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("job_id", sa.String(32), sa.ForeignKey("video_jobs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("artifact_id", sa.BigInteger(), sa.ForeignKey("artifacts.id", ondelete="SET NULL")),
        sa.Column("kind", sa.String(64), nullable=False),
        sa.Column("source_name", sa.String(255), nullable=False),
        sa.Column("raw_text", sa.Text(), nullable=False),
        sa.Column("parsed_data", postgresql.JSONB(astext_type=sa.Text())),
        sa.Column("parse_status", sa.String(16), nullable=False),
        sa.Column("parse_error_code", sa.String(160)),
        sa.Column("parser_version", sa.String(32), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("byte_size", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("artifact_id", "sha256", name="uq_audit_documents_artifact_hash"),
        sa.UniqueConstraint(
            "job_id", "source_name", "sha256", name="uq_audit_documents_job_source_hash"
        ),
        sa.CheckConstraint("parse_status IN ('parsed', 'invalid')", name="ck_audit_documents_parse"),
        sa.CheckConstraint("length(sha256) = 64", name="ck_audit_documents_sha256_length"),
        sa.CheckConstraint("byte_size >= 0", name="ck_audit_documents_size_nonnegative"),
    )
    op.create_index("ix_audit_documents_job_kind", "audit_documents", ["job_id", "kind"])
    op.create_index("ix_audit_documents_created", "audit_documents", ["created_at"])

    op.create_table(
        "audit_reviews",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("job_id", sa.String(32), sa.ForeignKey("video_jobs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("verdict", sa.String(24), nullable=False),
        sa.Column("source", sa.String(12), nullable=False),
        sa.Column("reviewer_label", sa.String(120)),
        sa.Column("notes", sa.Text()),
        sa.Column("findings", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "verdict IN ('approved', 'rejected', 'needs_review')",
            name="ck_audit_reviews_verdict",
        ),
        sa.CheckConstraint("source IN ('system', 'agent', 'human')", name="ck_audit_reviews_source"),
    )
    op.create_index("ix_audit_reviews_job_created", "audit_reviews", ["job_id", "created_at"])
    op.create_index("ix_audit_reviews_verdict_created", "audit_reviews", ["verdict", "created_at"])


def downgrade() -> None:
    op.drop_table("audit_reviews")
    op.drop_table("audit_documents")
    op.drop_table("artifacts")
    op.drop_table("job_events")
    op.drop_table("video_jobs")
    op.drop_table("editing_sessions")
    op.drop_table("login_attempt_buckets")
    op.drop_table("auth_sessions")
