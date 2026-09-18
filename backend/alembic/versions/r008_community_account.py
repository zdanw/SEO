"""Persona communities belong to one Reddit account.

Revision ID: r008
Revises: r007
Create Date: 2026-09-18
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "r008_community_account"
down_revision = "r007_reddit_brand_products"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "reddit_communities",
        sa.Column("account_id", sa.Integer(), sa.ForeignKey("social_accounts.id"), nullable=True),
    )
    op.create_index("ix_reddit_communities_account_id", "reddit_communities", ["account_id"])


def downgrade() -> None:
    op.drop_index("ix_reddit_communities_account_id", table_name="reddit_communities")
    op.drop_column("reddit_communities", "account_id")
