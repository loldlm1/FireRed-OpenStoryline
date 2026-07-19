"""Add reusable session workspace contracts.

Revision ID: 20260719_0002
Revises: 20260717_0001
Create Date: 2026-07-19
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260719_0002"
down_revision: Union[str, Sequence[str], None] = "20260717_0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "editing_sessions",
        sa.Column("workflow_version", sa.Integer(), server_default="1", nullable=False),
    )
    op.create_check_constraint(
        "ck_editing_sessions_workflow_version",
        "editing_sessions",
        "workflow_version IN (1, 2)",
    )

    op.create_table(
        "session_input_videos",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column(
            "editing_session_id",
            sa.String(32),
            sa.ForeignKey("editing_sessions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("original_filename", sa.String(255), nullable=False),
        sa.Column("expected_size", sa.BigInteger(), nullable=False),
        sa.Column("received_bytes", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("media_type", sa.String(255)),
        sa.Column("relative_path", sa.String(1024)),
        sa.Column("sha256", sa.String(64)),
        sa.Column("failure_code", sa.String(80)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
        sa.Column("purged_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint(
            "editing_session_id", name="uq_session_input_videos_session"
        ),
        sa.CheckConstraint("id ~ '^[a-f0-9]{32}$'", name="ck_session_input_videos_id"),
        sa.CheckConstraint(
            "state IN ('pending', 'uploading', 'validating', 'ready', 'failed', 'expired', 'deleted')",
            name="ck_session_input_videos_state",
        ),
        sa.CheckConstraint(
            "length(btrim(original_filename)) > 0",
            name="ck_session_input_videos_filename_present",
        ),
        sa.CheckConstraint(
            "expected_size > 0", name="ck_session_input_videos_expected_size_positive"
        ),
        sa.CheckConstraint(
            "received_bytes >= 0 AND received_bytes <= expected_size",
            name="ck_session_input_videos_received_bytes",
        ),
        sa.CheckConstraint(
            "sha256 IS NULL OR length(sha256) = 64",
            name="ck_session_input_videos_sha256_length",
        ),
        sa.CheckConstraint(
            "state <> 'ready' OR (received_bytes = expected_size AND relative_path IS NOT NULL AND length(btrim(relative_path)) > 0 AND media_type IS NOT NULL AND length(btrim(media_type)) > 0 AND sha256 IS NOT NULL AND length(sha256) = 64 AND completed_at IS NOT NULL AND expires_at IS NOT NULL)",
            name="ck_session_input_videos_ready_metadata",
        ),
        sa.CheckConstraint(
            "relative_path IS NULL OR state IN ('ready', 'expired', 'deleted')",
            name="ck_session_input_videos_path_state",
        ),
    )
    op.create_index(
        "ix_session_input_videos_state_expiry",
        "session_input_videos",
        ["state", "expires_at"],
    )
    op.create_index(
        "ix_session_input_videos_updated", "session_input_videos", ["updated_at"]
    )

    op.create_table(
        "prompt_versions",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column(
            "editing_session_id",
            sa.String(32),
            sa.ForeignKey("editing_sessions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column(
            "settings_data",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "editing_session_id",
            "version_number",
            name="uq_prompt_versions_session_number",
        ),
        sa.CheckConstraint("id ~ '^[a-f0-9]{32}$'", name="ck_prompt_versions_id"),
        sa.CheckConstraint(
            "version_number >= 1", name="ck_prompt_versions_number_positive"
        ),
        sa.CheckConstraint(
            "length(btrim(prompt)) > 0 AND length(prompt) <= 12000",
            name="ck_prompt_versions_prompt",
        ),
    )
    op.create_index(
        "ix_prompt_versions_session_created",
        "prompt_versions",
        ["editing_session_id", "created_at", "id"],
    )

    op.add_column("video_jobs", sa.Column("prompt_version_id", sa.String(32)))
    op.add_column("video_jobs", sa.Column("attempt_number", sa.Integer()))
    op.add_column(
        "video_jobs",
        sa.Column("is_favorite", sa.Boolean(), server_default=sa.false(), nullable=False),
    )
    op.create_foreign_key(
        "fk_video_jobs_prompt_version_id",
        "video_jobs",
        "prompt_versions",
        ["prompt_version_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_check_constraint(
        "ck_video_jobs_attempt_positive",
        "video_jobs",
        "attempt_number IS NULL OR attempt_number >= 1",
    )
    op.create_check_constraint(
        "ck_video_jobs_favorite_versioned",
        "video_jobs",
        "NOT is_favorite OR (prompt_version_id IS NOT NULL AND attempt_number IS NOT NULL)",
    )
    op.create_unique_constraint(
        "uq_video_jobs_prompt_attempt",
        "video_jobs",
        ["prompt_version_id", "attempt_number"],
    )
    op.create_index(
        "uq_video_jobs_session_favorite",
        "video_jobs",
        ["editing_session_id"],
        unique=True,
        postgresql_where=sa.text("is_favorite AND deleted_at IS NULL"),
    )

    op.add_column(
        "job_events",
        sa.Column("audience", sa.String(16), server_default="internal", nullable=False),
    )
    op.create_check_constraint(
        "ck_job_events_audience",
        "job_events",
        "audience IN ('internal', 'user')",
    )
    op.create_index(
        "ix_job_events_job_audience_sequence",
        "job_events",
        ["job_id", "audience", "sequence"],
    )


def downgrade() -> None:
    op.drop_index("ix_job_events_job_audience_sequence", table_name="job_events")
    op.drop_constraint("ck_job_events_audience", "job_events", type_="check")
    op.drop_column("job_events", "audience")

    op.drop_index("uq_video_jobs_session_favorite", table_name="video_jobs")
    op.drop_constraint("uq_video_jobs_prompt_attempt", "video_jobs", type_="unique")
    op.drop_constraint("ck_video_jobs_favorite_versioned", "video_jobs", type_="check")
    op.drop_constraint("ck_video_jobs_attempt_positive", "video_jobs", type_="check")
    op.drop_constraint(
        "fk_video_jobs_prompt_version_id", "video_jobs", type_="foreignkey"
    )
    op.drop_column("video_jobs", "is_favorite")
    op.drop_column("video_jobs", "attempt_number")
    op.drop_column("video_jobs", "prompt_version_id")

    op.drop_index("ix_prompt_versions_session_created", table_name="prompt_versions")
    op.drop_table("prompt_versions")

    op.drop_index("ix_session_input_videos_updated", table_name="session_input_videos")
    op.drop_index(
        "ix_session_input_videos_state_expiry", table_name="session_input_videos"
    )
    op.drop_table("session_input_videos")

    op.drop_constraint(
        "ck_editing_sessions_workflow_version", "editing_sessions", type_="check"
    )
    op.drop_column("editing_sessions", "workflow_version")
