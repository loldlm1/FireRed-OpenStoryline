"""Add reusable pipeline checkpoint metadata.

Revision ID: 20260721_0003
Revises: 20260719_0002
Create Date: 2026-07-21
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260721_0003"
down_revision: Union[str, Sequence[str], None] = "20260719_0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "session_analysis_cache",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "editing_session_id",
            sa.String(32),
            sa.ForeignKey("editing_sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "input_video_id",
            sa.String(32),
            sa.ForeignKey("session_input_videos.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("stage", sa.String(64), nullable=False),
        sa.Column("contract_version", sa.String(64), nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("relative_path", sa.String(1024), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("byte_size", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column(
            "metadata_data",
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
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "input_video_id",
            "stage",
            "fingerprint",
            name="uq_session_analysis_cache_fingerprint",
        ),
        sa.CheckConstraint(
            "stage ~ '^[a-z0-9_]{1,64}$'",
            name="ck_session_analysis_cache_stage",
        ),
        sa.CheckConstraint(
            "length(fingerprint) = 64 AND length(sha256) = 64",
            name="ck_session_analysis_cache_hashes",
        ),
        sa.CheckConstraint(
            "byte_size >= 0", name="ck_session_analysis_cache_size_nonnegative"
        ),
        sa.CheckConstraint(
            "status IN ('available', 'quarantined')",
            name="ck_session_analysis_cache_status",
        ),
    )
    op.create_index(
        "ix_session_analysis_cache_lookup",
        "session_analysis_cache",
        ["editing_session_id", "input_video_id", "stage", "fingerprint"],
    )
    op.create_index(
        "ix_session_analysis_cache_expiry",
        "session_analysis_cache",
        ["expires_at"],
    )

    op.create_table(
        "job_stage_checkpoints",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "job_id",
            sa.String(32),
            sa.ForeignKey("video_jobs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("stage", sa.String(64), nullable=False),
        sa.Column("contract_version", sa.String(64), nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("relative_path", sa.String(1024), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("byte_size", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column(
            "reused_from_job_id",
            sa.String(32),
            sa.ForeignKey("video_jobs.id", ondelete="SET NULL"),
        ),
        sa.Column(
            "metadata_data",
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
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "job_id",
            "stage",
            "fingerprint",
            name="uq_job_stage_checkpoint_fingerprint",
        ),
        sa.CheckConstraint(
            "stage ~ '^[a-z0-9_]{1,64}$'",
            name="ck_job_stage_checkpoint_stage",
        ),
        sa.CheckConstraint(
            "length(fingerprint) = 64 AND length(sha256) = 64",
            name="ck_job_stage_checkpoint_hashes",
        ),
        sa.CheckConstraint(
            "byte_size >= 0", name="ck_job_stage_checkpoint_size_nonnegative"
        ),
        sa.CheckConstraint(
            "status IN ('available', 'quarantined')",
            name="ck_job_stage_checkpoint_status",
        ),
    )
    op.create_index(
        "ix_job_stage_checkpoint_lookup",
        "job_stage_checkpoints",
        ["job_id", "stage", "fingerprint"],
    )
    op.create_index(
        "ix_job_stage_checkpoint_expiry",
        "job_stage_checkpoints",
        ["expires_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_job_stage_checkpoint_expiry", table_name="job_stage_checkpoints")
    op.drop_index("ix_job_stage_checkpoint_lookup", table_name="job_stage_checkpoints")
    op.drop_table("job_stage_checkpoints")
    op.drop_index("ix_session_analysis_cache_expiry", table_name="session_analysis_cache")
    op.drop_index("ix_session_analysis_cache_lookup", table_name="session_analysis_cache")
    op.drop_table("session_analysis_cache")
