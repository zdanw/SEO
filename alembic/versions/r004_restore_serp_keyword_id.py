"""Restore serp_rank_snapshots.keyword_id after opportunity-engine schema drift.

Revision ID: r004_restore_serp_keyword_id
Revises: r003_restore_reddit_queues
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "r004_restore_serp_keyword_id"
down_revision: Union[str, None] = "r003_restore_reddit_queues"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _columns(table: str) -> set[str]:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if table not in insp.get_table_names():
        return set()
    return {c["name"] for c in insp.get_columns(table)}


def upgrade() -> None:
    cols = _columns("serp_rank_snapshots")
    if "keyword_id" in cols and "search_query_id" not in cols:
        return

    # Timescale hypertable PK cannot be altered in place. Old rows were keyed
    # by search_query_id and search_queries was dropped in r002, so they are
    # not usable by the current keyword monitor.
    op.execute(sa.text("DROP TABLE IF EXISTS serp_rank_snapshots CASCADE"))
    op.create_table(
        "serp_rank_snapshots",
        sa.Column("time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("keyword_id", sa.Integer(), nullable=False),
        sa.Column("target_url", sa.String(length=500), nullable=True),
        sa.Column("rank", sa.Integer(), nullable=True),
        sa.Column("page", sa.Integer(), nullable=True),
        sa.Column("serp_features", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("search_region", sa.String(length=10), nullable=False),
        sa.Column("proxy_used", sa.String(length=100), nullable=True),
        sa.Column("crawl_status", sa.String(length=20), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["keyword_id"], ["keywords.id"]),
        sa.PrimaryKeyConstraint("time", "keyword_id"),
    )
    op.create_index(
        "ix_serp_keyword_time",
        "serp_rank_snapshots",
        ["keyword_id", sa.text("time DESC")],
        unique=False,
    )
    op.create_index(
        "ix_serp_status_time",
        "serp_rank_snapshots",
        ["crawl_status", sa.text("time DESC")],
        unique=False,
    )
    op.execute(
        sa.text(
            "SELECT create_hypertable('serp_rank_snapshots', 'time', "
            "chunk_time_interval => INTERVAL '7 days', if_not_exists => true)"
        )
    )


def downgrade() -> None:
    op.execute(sa.text("DROP TABLE IF EXISTS serp_rank_snapshots CASCADE"))
    op.create_table(
        "serp_rank_snapshots",
        sa.Column("time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("search_query_id", sa.Integer(), nullable=False),
        sa.Column("target_url", sa.String(length=500), nullable=True),
        sa.Column("rank", sa.Integer(), nullable=True),
        sa.Column("page", sa.Integer(), nullable=True),
        sa.Column("serp_features", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("search_region", sa.String(length=10), nullable=False),
        sa.Column("proxy_used", sa.String(length=100), nullable=True),
        sa.Column("crawl_status", sa.String(length=20), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("time", "search_query_id"),
    )
    op.execute(
        sa.text(
            "SELECT create_hypertable('serp_rank_snapshots', 'time', "
            "chunk_time_interval => INTERVAL '7 days', if_not_exists => true)"
        )
    )
