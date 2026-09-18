"""Add Reddit content mix, persona JSON, product brief, community purpose.

Revision ID: r006_reddit_smart_ops
Revises: r005_restore_zernio_reddit_ops
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "r006_reddit_smart_ops"
down_revision: Union[str, None] = "r005_restore_zernio_reddit_ops"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "reddit_posts",
        sa.Column("content_intent", sa.String(length=20), nullable=False, server_default="casual"),
    )
    op.add_column(
        "reddit_comments",
        sa.Column("content_intent", sa.String(length=20), nullable=False, server_default="casual"),
    )
    op.add_column("reddit_comments", sa.Column("ai_risk", sa.String(length=20), nullable=True))
    op.add_column("reddit_account_profiles", sa.Column("persona_config", sa.JSON(), nullable=True))
    op.add_column(
        "reddit_communities",
        sa.Column("purpose", sa.String(length=20), nullable=False, server_default="persona"),
    )
    op.create_index("ix_reddit_communities_purpose", "reddit_communities", ["purpose"])
    op.create_table(
        "reddit_product_briefs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("site_id", sa.Integer(), nullable=False),
        sa.Column("brand", sa.String(length=120), nullable=False, server_default=""),
        sa.Column("category", sa.String(length=120), nullable=False, server_default=""),
        sa.Column("talking_points", sa.JSON(), nullable=True),
        sa.Column("competitors", sa.JSON(), nullable=True),
        sa.Column("never_claim", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["site_id"], ["client_sites.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("site_id"),
    )
    op.create_index("ix_reddit_product_briefs_site_id", "reddit_product_briefs", ["site_id"])


def downgrade() -> None:
    op.drop_index("ix_reddit_product_briefs_site_id", table_name="reddit_product_briefs")
    op.drop_table("reddit_product_briefs")
    op.drop_index("ix_reddit_communities_purpose", table_name="reddit_communities")
    op.drop_column("reddit_communities", "purpose")
    op.drop_column("reddit_account_profiles", "persona_config")
    op.drop_column("reddit_comments", "ai_risk")
    op.drop_column("reddit_comments", "content_intent")
    op.drop_column("reddit_posts", "content_intent")
