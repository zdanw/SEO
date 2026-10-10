"""Restore Zernio keys and Reddit ops tables dropped by r002.

Revision ID: r005_restore_zernio_reddit_ops
Revises: r004_restore_serp_keyword_id
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "r005_restore_zernio_reddit_ops"
down_revision: Union[str, None] = "r004_restore_serp_keyword_id"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "zernio_api_keys",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("label", sa.String(length=100), nullable=False),
        sa.Column("api_key", sa.Text(), nullable=False),
        sa.Column("profile_id", sa.String(length=100), nullable=True),
        sa.Column("is_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("api_key"),
    )

    op.add_column("reddit_posts", sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("reddit_posts", sa.Column("published_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_reddit_posts_scheduled_at", "reddit_posts", ["scheduled_at"])
    op.add_column("reddit_comments", sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("reddit_comments", sa.Column("published_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_reddit_comments_scheduled_at", "reddit_comments", ["scheduled_at"])

    op.create_table(
        "reddit_account_profiles",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("site_id", sa.Integer(), nullable=False),
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(length=20), nullable=False),
        sa.Column("stage", sa.String(length=30), nullable=False),
        sa.Column("karma", sa.Integer(), nullable=False),
        sa.Column("persona", sa.String(length=200), nullable=True),
        sa.Column("daily_post_limit", sa.Integer(), nullable=False),
        sa.Column("daily_comment_limit", sa.Integer(), nullable=False),
        sa.Column("risk_status", sa.String(length=20), nullable=False),
        sa.Column("risk_reason", sa.Text(), nullable=True),
        sa.Column("last_warning_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("karma_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["account_id"], ["social_accounts.id"]),
        sa.ForeignKeyConstraint(["site_id"], ["client_sites.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("account_id"),
    )
    op.create_index("ix_reddit_account_profiles_site_id", "reddit_account_profiles", ["site_id"])
    op.create_index("ix_reddit_account_profiles_account_id", "reddit_account_profiles", ["account_id"])
    op.create_index("ix_reddit_account_profiles_role", "reddit_account_profiles", ["role"])

    op.create_table(
        "reddit_communities",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("site_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("category", sa.String(length=20), nullable=False),
        sa.Column("rules_note", sa.Text(), nullable=True),
        sa.Column("allows_links", sa.Boolean(), nullable=False),
        sa.Column("promo_weekday", sa.Integer(), nullable=True),
        sa.Column("daily_post_limit", sa.Integer(), nullable=False),
        sa.Column("best_hour_utc", sa.Integer(), nullable=True),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["site_id"], ["client_sites.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_reddit_communities_site_id", "reddit_communities", ["site_id"])
    op.create_index("ix_reddit_communities_name", "reddit_communities", ["name"])

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

    op.create_table(
        "reddit_post_metrics",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("post_id", sa.Integer(), nullable=False),
        sa.Column("score", sa.Integer(), nullable=False),
        sa.Column("num_comments", sa.Integer(), nullable=False),
        sa.Column("upvote_ratio", sa.Float(), nullable=True),
        sa.Column("synced_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["post_id"], ["reddit_posts.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_reddit_post_metrics_post_id", "reddit_post_metrics", ["post_id"])


def downgrade() -> None:
    op.drop_index("ix_reddit_post_metrics_post_id", table_name="reddit_post_metrics")
    op.drop_table("reddit_post_metrics")
    op.drop_index("ix_reddit_keywords_category", table_name="reddit_keywords")
    op.drop_index("ix_reddit_keywords_keyword", table_name="reddit_keywords")
    op.drop_index("ix_reddit_keywords_site_id", table_name="reddit_keywords")
    op.drop_table("reddit_keywords")
    op.drop_index("ix_reddit_communities_name", table_name="reddit_communities")
    op.drop_index("ix_reddit_communities_site_id", table_name="reddit_communities")
    op.drop_table("reddit_communities")
    op.drop_index("ix_reddit_account_profiles_role", table_name="reddit_account_profiles")
    op.drop_index("ix_reddit_account_profiles_account_id", table_name="reddit_account_profiles")
    op.drop_index("ix_reddit_account_profiles_site_id", table_name="reddit_account_profiles")
    op.drop_table("reddit_account_profiles")
    op.drop_index("ix_reddit_comments_scheduled_at", table_name="reddit_comments")
    op.drop_column("reddit_comments", "published_at")
    op.drop_column("reddit_comments", "scheduled_at")
    op.drop_index("ix_reddit_posts_scheduled_at", table_name="reddit_posts")
    op.drop_column("reddit_posts", "published_at")
    op.drop_column("reddit_posts", "scheduled_at")
    op.drop_table("zernio_api_keys")
