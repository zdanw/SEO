"""Add community official rules_text fields.

Revision ID: r014
Revises: r013_product_description
Create Date: 2026-09-22
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "r014_community_rules"
down_revision = "r013_product_description"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("reddit_communities", sa.Column("rules_text", sa.Text(), nullable=True))
    op.add_column(
        "reddit_communities",
        sa.Column("rules_fetched_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("reddit_communities", "rules_fetched_at")
    op.drop_column("reddit_communities", "rules_text")
