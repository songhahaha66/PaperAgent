"""add run_events table

Revision ID: e5a91c3d7b24
Revises: c4e8a1b2d9f0
Create Date: 2026-09-19 03:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e5a91c3d7b24"
down_revision: Union[str, Sequence[str], None] = "c4e8a1b2d9f0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "run_events",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("run_id", sa.String(length=100), nullable=False),
        sa.Column("thread_id", sa.String(length=100), nullable=False),
        sa.Column("offset", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(length=50), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_run_events_id"), "run_events", ["id"], unique=False)
    op.create_index(op.f("ix_run_events_run_id"), "run_events", ["run_id"], unique=False)
    op.create_index(op.f("ix_run_events_thread_id"), "run_events", ["thread_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_run_events_thread_id"), table_name="run_events")
    op.drop_index(op.f("ix_run_events_run_id"), table_name="run_events")
    op.drop_index(op.f("ix_run_events_id"), table_name="run_events")
    op.drop_table("run_events")
