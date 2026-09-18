"""Restore reddit_posts / reddit_comments dropped by r002.

Revision ID: r003_restore_reddit_queues
Revises: r002_trim_modules
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "r003_restore_reddit_queues"
down_revision: Union[str, None] = "r002_trim_modules"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "reddit_posts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("site_id", sa.Integer(), nullable=False),
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("post_type", sa.String(length=20), nullable=False),
        sa.Column("subreddit", sa.String(length=100), nullable=False),
        sa.Column("keyword", sa.String(length=200), nullable=False),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("site_url", sa.String(length=500), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("reddit_post_id", sa.String(length=50), nullable=True),
        sa.Column("reddit_permalink", sa.String(length=500), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["account_id"], ["social_accounts.id"]),
        sa.ForeignKeyConstraint(["site_id"], ["client_sites.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_reddit_posts_site_id", "reddit_posts", ["site_id"])
    op.create_index("ix_reddit_posts_account_id", "reddit_posts", ["account_id"])
    op.create_index("ix_reddit_posts_status", "reddit_posts", ["status"])

    op.create_table(
        "reddit_comments",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("site_id", sa.Integer(), nullable=False),
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("target_post_url", sa.String(length=500), nullable=False),
        sa.Column("target_thing_id", sa.String(length=20), nullable=False),
        sa.Column("subreddit", sa.String(length=100), nullable=False),
        sa.Column("target_post_title", sa.String(length=500), nullable=True),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("keyword", sa.String(length=200), nullable=True),
        sa.Column("discover_source", sa.String(length=20), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("reddit_comment_id", sa.String(length=50), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["account_id"], ["social_accounts.id"]),
        sa.ForeignKeyConstraint(["site_id"], ["client_sites.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_reddit_comments_site_id", "reddit_comments", ["site_id"])
    op.create_index("ix_reddit_comments_account_id", "reddit_comments", ["account_id"])
    op.create_index("ix_reddit_comments_status", "reddit_comments", ["status"])


def downgrade() -> None:
    op.drop_index("ix_reddit_comments_status", table_name="reddit_comments")
    op.drop_index("ix_reddit_comments_account_id", table_name="reddit_comments")
    op.drop_index("ix_reddit_comments_site_id", table_name="reddit_comments")
    op.drop_table("reddit_comments")
    op.drop_index("ix_reddit_posts_status", table_name="reddit_posts")
    op.drop_index("ix_reddit_posts_account_id", table_name="reddit_posts")
    op.drop_index("ix_reddit_posts_site_id", table_name="reddit_posts")
    op.drop_table("reddit_posts")
