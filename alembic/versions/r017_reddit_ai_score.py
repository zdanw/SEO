"""Reddit content AI detection: persist ai_risk/ai_score on posts and comments.

Revision ID: r017_reddit_ai_score
Revises: r016_reddit_campaign
Create Date: 2026-10-10
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "r017_reddit_ai_score"
down_revision = "r016_reddit_campaign"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("reddit_posts", sa.Column("ai_risk", sa.String(length=20), nullable=True))
    op.add_column("reddit_posts", sa.Column("ai_score", sa.Float(), nullable=True))
    op.add_column("reddit_comments", sa.Column("ai_score", sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column("reddit_comments", "ai_score")
    op.drop_column("reddit_posts", "ai_score")
    op.drop_column("reddit_posts", "ai_risk")
