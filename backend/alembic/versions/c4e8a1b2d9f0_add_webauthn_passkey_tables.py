"""add webauthn passkey tables

Revision ID: c4e8a1b2d9f0
Revises: 906ca23fbfe1
Create Date: 2026-08-24 07:10:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c4e8a1b2d9f0"
down_revision: Union[str, Sequence[str], None] = "906ca23fbfe1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "webauthn_credentials",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("credential_id", sa.String(length=1024), nullable=False),
        sa.Column("public_key", sa.Text(), nullable=False),
        sa.Column("sign_count", sa.Integer(), nullable=False),
        sa.Column("device_type", sa.String(length=32), nullable=True),
        sa.Column("backed_up", sa.Boolean(), nullable=True),
        sa.Column("transports", sa.JSON(), nullable=True),
        sa.Column("aaguid", sa.String(length=64), nullable=True),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_webauthn_credentials_id"), "webauthn_credentials", ["id"], unique=False)
    op.create_index(op.f("ix_webauthn_credentials_user_id"), "webauthn_credentials", ["user_id"], unique=False)
    op.create_index(
        op.f("ix_webauthn_credentials_credential_id"),
        "webauthn_credentials",
        ["credential_id"],
        unique=True,
    )

    op.create_table(
        "webauthn_challenges",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("challenge_id", sa.String(length=64), nullable=False),
        sa.Column("challenge", sa.String(length=255), nullable=False),
        sa.Column("challenge_type", sa.String(length=32), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_webauthn_challenges_id"), "webauthn_challenges", ["id"], unique=False)
    op.create_index(
        op.f("ix_webauthn_challenges_challenge_id"),
        "webauthn_challenges",
        ["challenge_id"],
        unique=True,
    )
    op.create_index(op.f("ix_webauthn_challenges_user_id"), "webauthn_challenges", ["user_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_webauthn_challenges_user_id"), table_name="webauthn_challenges")
    op.drop_index(op.f("ix_webauthn_challenges_challenge_id"), table_name="webauthn_challenges")
    op.drop_index(op.f("ix_webauthn_challenges_id"), table_name="webauthn_challenges")
    op.drop_table("webauthn_challenges")
    op.drop_index(op.f("ix_webauthn_credentials_credential_id"), table_name="webauthn_credentials")
    op.drop_index(op.f("ix_webauthn_credentials_user_id"), table_name="webauthn_credentials")
    op.drop_index(op.f("ix_webauthn_credentials_id"), table_name="webauthn_credentials")
    op.drop_table("webauthn_credentials")
