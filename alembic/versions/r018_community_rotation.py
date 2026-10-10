"""Reddit community rotation ledger + pool expansion trace.

Revision ID: r018_community_rotation
Revises: r017_reddit_ai_score
Create Date: 2026-10-10
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "r018_community_rotation"
down_revision = "r017_reddit_ai_score"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "reddit_communities",
        sa.Column("discovered_from", sa.String(length=100), nullable=True),
    )
    op.create_table(
        "reddit_community_activity",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("site_id", sa.Integer(), sa.ForeignKey("client_sites.id"), nullable=False),
        sa.Column(
            "account_id",
            sa.Integer(),
            sa.ForeignKey("social_accounts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("subreddit", sa.String(length=100), nullable=False),
        sa.Column("last_action_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("action_count_7d", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("action_count_30d", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("penalty", sa.Float(), nullable=False, server_default="0"),
        sa.Column("cooldown_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("recent_angles", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("account_id", "subreddit", name="uq_community_activity_account_sub"),
    )
    op.create_index(
        "ix_reddit_community_activity_site_id",
        "reddit_community_activity",
        ["site_id"],
    )
    op.create_index(
        "ix_reddit_community_activity_account_id",
        "reddit_community_activity",
        ["account_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_reddit_community_activity_account_id", table_name="reddit_community_activity")
    op.drop_index("ix_reddit_community_activity_site_id", table_name="reddit_community_activity")
    op.drop_table("reddit_community_activity")
    op.drop_column("reddit_communities", "discovered_from")
