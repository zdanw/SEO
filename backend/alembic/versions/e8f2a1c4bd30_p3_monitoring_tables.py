"""P3: serp_rank_snapshots, competitor_rank_snapshots, crawler_logs, backlinks, competitors, recommendations

Revision ID: e8f2a1c4bd30
Revises: aaf3dc7ddeca
Create Date: 2026-08-24 16:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = 'e8f2a1c4bd30'
down_revision: Union[str, None] = 'aaf3dc7ddeca'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. 启用 TimescaleDB 扩展（幂等）
    op.execute("CREATE EXTENSION IF NOT EXISTS timescaledb")

    # 2. 普通表：competitors
    op.create_table('competitors',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('domain', sa.String(length=200), nullable=False),
        sa.Column('name', sa.String(length=100), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('user_id', 'domain', name='uq_competitors_user_domain'),
    )
    op.create_index('ix_competitors_user_id', 'competitors', ['user_id'], unique=False)

    # 3. 普通表：backlinks
    op.create_table('backlinks',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('target_url', sa.String(length=500), nullable=False),
        sa.Column('source_url', sa.String(length=500), nullable=False),
        sa.Column('anchor_text', sa.String(length=300), nullable=True),
        sa.Column('domain_authority', sa.Integer(), nullable=True),
        sa.Column('is_alive', sa.Boolean(), nullable=False),
        sa.Column('first_seen_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('last_checked_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('lost_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('target_url', 'source_url', name='uq_backlinks_target_source'),
    )
    op.create_index('ix_backlinks_user_id', 'backlinks', ['user_id'], unique=False)

    # 4. 普通表：crawler_logs（BIGINT 主键，写入量大）
    op.create_table('crawler_logs',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=True),
        sa.Column('url_path', sa.String(length=500), nullable=False),
        sa.Column('crawler_name', sa.String(length=50), nullable=False),
        sa.Column('status_code', sa.Integer(), nullable=False),
        sa.Column('user_agent', sa.Text(), nullable=True),
        sa.Column('ip', sa.String(length=45), nullable=True),
        sa.Column('crawled_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_crawler_logs_user_id', 'crawler_logs', ['user_id'], unique=False)
    op.create_index('ix_crawler_logs_crawler_name', 'crawler_logs', ['crawler_name'], unique=False)
    op.create_index('ix_crawler_logs_crawled_at', 'crawler_logs', ['crawled_at'], unique=False)

    # 5. 普通表：recommendations
    op.create_table('recommendations',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=True),
        sa.Column('article_id', sa.Integer(), nullable=True),
        sa.Column('keyword_id', sa.Integer(), nullable=True),
        sa.Column('category', sa.String(length=50), nullable=False),
        sa.Column('severity', sa.String(length=20), nullable=False),
        sa.Column('title', sa.String(length=300), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('suggestion', sa.Text(), nullable=True),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('resolved_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['users.id']),
        sa.ForeignKeyConstraint(['article_id'], ['articles.id']),
        sa.ForeignKeyConstraint(['keyword_id'], ['keywords.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_recommendations_user_id', 'recommendations', ['user_id'], unique=False)
    op.create_index('ix_recommendations_status', 'recommendations', ['status'], unique=False)

    # 6. Hypertable 表：serp_rank_snapshots（先建表 + 索引，再转 hypertable）
    op.create_table('serp_rank_snapshots',
        sa.Column('time', sa.DateTime(timezone=True), nullable=False),
        sa.Column('keyword_id', sa.Integer(), nullable=False),
        sa.Column('target_url', sa.String(length=500), nullable=True),
        sa.Column('rank', sa.Integer(), nullable=True),
        sa.Column('page', sa.Integer(), nullable=True),
        sa.Column('serp_features', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('search_region', sa.String(length=10), nullable=False),
        sa.Column('proxy_used', sa.String(length=100), nullable=True),
        sa.Column('crawl_status', sa.String(length=20), nullable=False),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(['keyword_id'], ['keywords.id']),
        sa.PrimaryKeyConstraint('time', 'keyword_id'),
    )
    op.create_index('ix_serp_keyword_time', 'serp_rank_snapshots', ['keyword_id', sa.text('time DESC')], unique=False)
    op.create_index('ix_serp_status_time', 'serp_rank_snapshots', ['crawl_status', sa.text('time DESC')], unique=False)

    # 7. Hypertable 表：competitor_rank_snapshots
    op.create_table('competitor_rank_snapshots',
        sa.Column('time', sa.DateTime(timezone=True), nullable=False),
        sa.Column('competitor_id', sa.Integer(), nullable=False),
        sa.Column('keyword_id', sa.Integer(), nullable=False),
        sa.Column('domain', sa.String(length=200), nullable=False),
        sa.Column('rank', sa.Integer(), nullable=True),
        sa.Column('target_url', sa.String(length=500), nullable=True),
        sa.ForeignKeyConstraint(['competitor_id'], ['competitors.id']),
        sa.ForeignKeyConstraint(['keyword_id'], ['keywords.id']),
        sa.PrimaryKeyConstraint('time', 'competitor_id', 'keyword_id'),
    )
    op.create_index('ix_competitor_rank_comp_kw_time', 'competitor_rank_snapshots',
                    ['competitor_id', 'keyword_id', sa.text('time DESC')], unique=False)

    # 8. 转换为 hypertable（关键步骤）
    op.execute("SELECT create_hypertable('serp_rank_snapshots', 'time', chunk_time_interval => INTERVAL '7 days', if_not_exists => true)")
    op.execute("SELECT create_hypertable('competitor_rank_snapshots', 'time', chunk_time_interval => INTERVAL '7 days', if_not_exists => true)")


def downgrade() -> None:
    # hypertable 用 CASCADE 删除所有 chunk
    op.execute("DROP TABLE IF EXISTS competitor_rank_snapshots CASCADE")
    op.execute("DROP TABLE IF EXISTS serp_rank_snapshots CASCADE")
    op.drop_index('ix_recommendations_status', table_name='recommendations')
    op.drop_index('ix_recommendations_user_id', table_name='recommendations')
    op.drop_table('recommendations')
    op.drop_index('ix_crawler_logs_crawled_at', table_name='crawler_logs')
    op.drop_index('ix_crawler_logs_crawler_name', table_name='crawler_logs')
    op.drop_index('ix_crawler_logs_user_id', table_name='crawler_logs')
    op.drop_table('crawler_logs')
    op.drop_index('ix_backlinks_user_id', table_name='backlinks')
    op.drop_table('backlinks')
    op.drop_index('ix_competitors_user_id', table_name='competitors')
    op.drop_table('competitors')
