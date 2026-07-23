"""Make reusable Agentic sessions the database default.

Revision ID: 20260723_0004
Revises: 20260721_0003
Create Date: 2026-07-23
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260723_0004"
down_revision: Union[str, Sequence[str], None] = "20260721_0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Existing workflow-v1 rows remain unchanged as non-executable audit history.
    op.alter_column(
        "editing_sessions",
        "workflow_version",
        existing_type=sa.Integer(),
        existing_nullable=False,
        server_default=sa.text("2"),
    )


def downgrade() -> None:
    op.alter_column(
        "editing_sessions",
        "workflow_version",
        existing_type=sa.Integer(),
        existing_nullable=False,
        server_default=sa.text("1"),
    )
