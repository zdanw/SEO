"""Drop unused modules; keep dashboard / social / SERP tables.

Revision ID: r002_trim_modules
Revises: r001_opportunity_engine
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "r002_trim_modules"
down_revision: Union[str, None] = "r001_opportunity_engine"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

UNUSED_TABLES = [
    "content_claims",
    "claim_evidence",
    "claims",
    "research_evidence",
    "research_projects",
    "ai_citations",
    "ai_search_results",
    "ai_visibility_daily",
    "ai_search_runs",
    "ai_search_queries",
    "search_observations",
    "gsc_daily_metrics",
    "content_versions",
    "content_asset_queries",
    "content_asset_topics",
    "content_assets",
    "search_queries",
    "topics",
    "recommendation_events",
    "recommendation_actions",
    "team_tasks",
    "experiments",
    "technical_audits",
    "reddit_community_rule_versions",
    "reddit_opportunities",
    "reddit_threads",
    "reddit_post_metrics",
    "reddit_keywords",
    "reddit_communities",
    "reddit_account_profiles",
    "reddit_comments",
    "reddit_posts",
    "gsc_connections",
    "seo_audit_pages",
    "seo_audits",
    "internal_links",
    "articles",
    "recommendations",
    "backlinks",
    "competitor_rank_snapshots",
    "competitors",
    "idempotency_keys",
    "zernio_api_keys",
    "crawler_logs",
]


def upgrade() -> None:
    op.execute(sa.text("ALTER TABLE social_posts DROP COLUMN IF EXISTS article_id"))
    for table in UNUSED_TABLES:
        op.execute(sa.text(f"DROP TABLE IF EXISTS {table} CASCADE"))


def downgrade() -> None:
    op.add_column(
        "social_posts",
        sa.Column("article_id", sa.Integer(), nullable=True),
    )
    op.create_index("ix_social_posts_article_id", "social_posts", ["article_id"])
