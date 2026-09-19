"""Product-community many-to-many binding.

Revision ID: r010
Revises: r009
Create Date: 2026-09-19
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "r010_product_communities"
down_revision = "r009_community_verify"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "reddit_product_communities",
        sa.Column("product_id", sa.Integer(), sa.ForeignKey("reddit_products.id", ondelete="CASCADE"), primary_key=True),
        sa.Column(
            "community_id",
            sa.Integer(),
            sa.ForeignKey("reddit_communities.id", ondelete="CASCADE"),
            primary_key=True,
        ),
    )


def downgrade() -> None:
    op.drop_table("reddit_product_communities")
