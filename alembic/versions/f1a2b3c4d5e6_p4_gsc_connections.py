"""P4: gsc_connections table for Google Search Console OAuth

Revision ID: f1a2b3c4d5e6
Revises: e8f2a1c4bd30
Create Date: 2026-09-01 11:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f1a2b3c4d5e6"
down_revision: Union[str, None] = "e8f2a1c4bd30"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "gsc_connections",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("access_token", sa.Text(), nullable=True),
        sa.Column("refresh_token", sa.Text(), nullable=True),
        sa.Column("token_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("site_url", sa.String(length=500), nullable=True),
        sa.Column("google_email", sa.String(length=200), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id"),
    )
    op.create_index(op.f("ix_gsc_connections_user_id"), "gsc_connections", ["user_id"], unique=True)


def downgrade() -> None:
    op.drop_index(op.f("ix_gsc_connections_user_id"), table_name="gsc_connections")
    op.drop_table("gsc_connections")
