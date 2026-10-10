"""Add product description for LLM context.

Revision ID: r013
Revises: r012_product_keywords
Create Date: 2026-09-21
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "r013_product_description"
down_revision = "r012_product_keywords"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "reddit_products",
        sa.Column("description", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("reddit_products", "description")
