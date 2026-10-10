"""Product keywords table; drop site-level reddit_keywords.

Revision ID: r012
Revises: r011_task_runs_ops
Create Date: 2026-09-21
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "r012_product_keywords"
down_revision = "r011_task_runs_ops"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "reddit_product_keywords",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "product_id",
            sa.Integer(),
            sa.ForeignKey("reddit_products.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("keyword", sa.String(length=200), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_reddit_product_keywords_product_id",
        "reddit_product_keywords",
        ["product_id"],
    )
    op.create_index(
        "uq_reddit_product_keywords_product_keyword",
        "reddit_product_keywords",
        ["product_id", "keyword"],
        unique=True,
    )

    op.drop_index("ix_reddit_keywords_category", table_name="reddit_keywords")
    op.drop_index("ix_reddit_keywords_keyword", table_name="reddit_keywords")
    op.drop_index("ix_reddit_keywords_site_id", table_name="reddit_keywords")
    op.drop_table("reddit_keywords")


def downgrade() -> None:
    op.create_table(
        "reddit_keywords",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("site_id", sa.Integer(), nullable=False),
        sa.Column("keyword", sa.String(length=200), nullable=False),
        sa.Column("category", sa.String(length=20), nullable=False),
        sa.Column("intent_note", sa.String(length=300), nullable=True),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("used_count", sa.Integer(), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["site_id"], ["client_sites.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_reddit_keywords_site_id", "reddit_keywords", ["site_id"])
    op.create_index("ix_reddit_keywords_keyword", "reddit_keywords", ["keyword"])
    op.create_index("ix_reddit_keywords_category", "reddit_keywords", ["category"])

    op.drop_index(
        "uq_reddit_product_keywords_product_keyword",
        table_name="reddit_product_keywords",
    )
    op.drop_index("ix_reddit_product_keywords_product_id", table_name="reddit_product_keywords")
    op.drop_table("reddit_product_keywords")
