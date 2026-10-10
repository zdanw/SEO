"""Reddit automation campaign: campaigns / actions / vote logs.

Revision ID: r016_reddit_campaign
Revises: r015_zernio_key_site_id
Create Date: 2026-09-29
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "r016_reddit_campaign"
down_revision = "r015_zernio_key_site_id"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "reddit_campaigns",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("site_id", sa.Integer(), sa.ForeignKey("client_sites.id"), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="draft"),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=False),
        sa.Column("active_hour_start", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("active_hour_end", sa.Integer(), nullable=False, server_default="23"),
        sa.Column("content_mode", sa.String(length=20), nullable=False, server_default="review"),
        sa.Column("daily_posts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("daily_comments", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("daily_upvotes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("self_upvote_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("self_upvote_delay_minutes", sa.Integer(), nullable=False, server_default="240"),
        sa.Column("subreddits", sa.JSON(), nullable=True),
        sa.Column("account_ids", sa.JSON(), nullable=True),
        sa.Column("brand_id", sa.Integer(), sa.ForeignKey("reddit_brands.id"), nullable=True),
        sa.Column("product_id", sa.Integer(), sa.ForeignKey("reddit_products.id"), nullable=True),
        sa.Column("last_tick_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index("ix_reddit_campaigns_site_id", "reddit_campaigns", ["site_id"])
    op.create_index("ix_reddit_campaigns_status", "reddit_campaigns", ["status"])

    op.create_table(
        "reddit_campaign_actions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "campaign_id",
            sa.Integer(),
            sa.ForeignKey("reddit_campaigns.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("site_id", sa.Integer(), sa.ForeignKey("client_sites.id"), nullable=False),
        sa.Column(
            "account_id", sa.Integer(), sa.ForeignKey("social_accounts.id"), nullable=False
        ),
        sa.Column("action_type", sa.String(length=30), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="pending"),
        sa.Column("planned_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("executed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "post_id",
            sa.Integer(),
            sa.ForeignKey("reddit_posts.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "comment_id",
            sa.Integer(),
            sa.ForeignKey("reddit_comments.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("target_thing_id", sa.String(length=50), nullable=True),
        sa.Column("target_post_url", sa.String(length=500), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("skip_reason", sa.String(length=200), nullable=True),
        sa.Column("dedup_key", sa.String(length=150), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("campaign_id", "dedup_key", name="uq_campaign_action_dedup"),
    )
    op.create_index("ix_reddit_campaign_actions_campaign_id", "reddit_campaign_actions", ["campaign_id"])
    op.create_index("ix_reddit_campaign_actions_site_id", "reddit_campaign_actions", ["site_id"])
    op.create_index("ix_reddit_campaign_actions_account_id", "reddit_campaign_actions", ["account_id"])
    op.create_index("ix_reddit_campaign_actions_action_type", "reddit_campaign_actions", ["action_type"])
    op.create_index("ix_reddit_campaign_actions_status", "reddit_campaign_actions", ["status"])
    op.create_index("ix_reddit_campaign_actions_planned_at", "reddit_campaign_actions", ["planned_at"])
    op.create_index("ix_reddit_campaign_actions_post_id", "reddit_campaign_actions", ["post_id"])
    op.create_index("ix_reddit_campaign_actions_comment_id", "reddit_campaign_actions", ["comment_id"])

    op.create_table(
        "reddit_vote_logs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("site_id", sa.Integer(), sa.ForeignKey("client_sites.id"), nullable=False),
        sa.Column("account_id", sa.Integer(), sa.ForeignKey("social_accounts.id"), nullable=False),
        sa.Column("thing_id", sa.String(length=50), nullable=False),
        sa.Column("direction", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("source", sa.String(length=20), nullable=False, server_default="manual"),
        sa.Column(
            "campaign_action_id",
            sa.Integer(),
            sa.ForeignKey("reddit_campaign_actions.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("account_id", "thing_id", name="uq_vote_log_account_thing"),
    )
    op.create_index("ix_reddit_vote_logs_site_id", "reddit_vote_logs", ["site_id"])
    op.create_index("ix_reddit_vote_logs_account_id", "reddit_vote_logs", ["account_id"])

    # 内容关联 campaign（删除 campaign 保留内容）
    op.add_column(
        "reddit_posts",
        sa.Column(
            "campaign_id",
            sa.Integer(),
            sa.ForeignKey("reddit_campaigns.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index("ix_reddit_posts_campaign_id", "reddit_posts", ["campaign_id"])
    op.add_column(
        "reddit_comments",
        sa.Column(
            "campaign_id",
            sa.Integer(),
            sa.ForeignKey("reddit_campaigns.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index("ix_reddit_comments_campaign_id", "reddit_comments", ["campaign_id"])


def downgrade() -> None:
    op.drop_index("ix_reddit_comments_campaign_id", table_name="reddit_comments")
    op.drop_column("reddit_comments", "campaign_id")
    op.drop_index("ix_reddit_posts_campaign_id", table_name="reddit_posts")
    op.drop_column("reddit_posts", "campaign_id")

    op.drop_index("ix_reddit_vote_logs_account_id", table_name="reddit_vote_logs")
    op.drop_index("ix_reddit_vote_logs_site_id", table_name="reddit_vote_logs")
    op.drop_table("reddit_vote_logs")

    op.drop_index("ix_reddit_campaign_actions_comment_id", table_name="reddit_campaign_actions")
    op.drop_index("ix_reddit_campaign_actions_post_id", table_name="reddit_campaign_actions")
    op.drop_index("ix_reddit_campaign_actions_planned_at", table_name="reddit_campaign_actions")
    op.drop_index("ix_reddit_campaign_actions_status", table_name="reddit_campaign_actions")
    op.drop_index("ix_reddit_campaign_actions_action_type", table_name="reddit_campaign_actions")
    op.drop_index("ix_reddit_campaign_actions_account_id", table_name="reddit_campaign_actions")
    op.drop_index("ix_reddit_campaign_actions_site_id", table_name="reddit_campaign_actions")
    op.drop_index("ix_reddit_campaign_actions_campaign_id", table_name="reddit_campaign_actions")
    op.drop_table("reddit_campaign_actions")

    op.drop_index("ix_reddit_campaigns_status", table_name="reddit_campaigns")
    op.drop_index("ix_reddit_campaigns_site_id", table_name="reddit_campaigns")
    op.drop_table("reddit_campaigns")
