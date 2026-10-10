"""Add community verify/activity fields.

Revision ID: r009
Revises: r008
Create Date: 2026-09-19
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "r009_community_verify"
down_revision = "r008_community_account"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("reddit_communities", sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("reddit_communities", sa.Column("exists", sa.Boolean(), nullable=True))
    op.add_column("reddit_communities", sa.Column("subscribers", sa.Integer(), nullable=True))
    op.add_column("reddit_communities", sa.Column("accounts_active", sa.Integer(), nullable=True))
    op.add_column("reddit_communities", sa.Column("posts_7d", sa.Integer(), nullable=True))
    op.add_column("reddit_communities", sa.Column("activity_score", sa.Float(), nullable=True))
    op.add_column("reddit_communities", sa.Column("verify_error", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("reddit_communities", "verify_error")
    op.drop_column("reddit_communities", "activity_score")
    op.drop_column("reddit_communities", "posts_7d")
    op.drop_column("reddit_communities", "accounts_active")
    op.drop_column("reddit_communities", "subscribers")
    op.drop_column("reddit_communities", "exists")
    op.drop_column("reddit_communities", "verified_at")
