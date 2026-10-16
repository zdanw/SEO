"""Content feedback columns + scheduling composite indexes + activity lowercase.

Revision ID: r019_content_feedback
Revises: r018_community_rotation
Create Date: 2026-10-13

- reddit_posts.narrative_angle: 内容级表现反馈加权的数据基础
- reddit_vote_logs.score_at_vote: 投票效果验证 / 自赞后分数异常衰减检测
- reddit_campaign_actions / reddit_posts / reddit_comments 复合索引：排期引擎与发布扫描高频查询
- reddit_community_activity.subreddit 统一小写（先去重再归一，避免唯一约束冲突）
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "r019_content_feedback"
down_revision = "r018_community_rotation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "reddit_posts",
        sa.Column("narrative_angle", sa.String(length=40), nullable=True),
    )
    op.add_column(
        "reddit_vote_logs",
        sa.Column("score_at_vote", sa.Integer(), nullable=True),
    )

    op.create_index(
        "ix_campaign_actions_account_status_planned",
        "reddit_campaign_actions",
        ["account_id", "status", "planned_at"],
    )
    op.create_index(
        "ix_campaign_actions_campaign_planned",
        "reddit_campaign_actions",
        ["campaign_id", "planned_at"],
    )
    op.create_index(
        "ix_reddit_posts_status_scheduled",
        "reddit_posts",
        ["status", "scheduled_at"],
    )
    op.create_index(
        "ix_reddit_comments_status_scheduled",
        "reddit_comments",
        ["status", "scheduled_at"],
    )

    # 社区活动账本 subreddit 统一小写：先删除仅大小写不同的重复行（保留最小 id），
    # 再归一化；运行时查询已按 lower() 匹配，此为数据规范化
    bind = op.get_bind()
    bind.execute(
        sa.text(
            """
            DELETE FROM reddit_community_activity
            WHERE id NOT IN (
                SELECT MIN(id) FROM reddit_community_activity GROUP BY account_id, LOWER(subreddit)
            )
            """
        )
    )
    bind.execute(
        sa.text(
            "UPDATE reddit_community_activity SET subreddit = LOWER(subreddit) "
            "WHERE subreddit != LOWER(subreddit)"
        )
    )


def downgrade() -> None:
    op.drop_index("ix_reddit_comments_status_scheduled", table_name="reddit_comments")
    op.drop_index("ix_reddit_posts_status_scheduled", table_name="reddit_posts")
    op.drop_index("ix_campaign_actions_campaign_planned", table_name="reddit_campaign_actions")
    op.drop_index(
        "ix_campaign_actions_account_status_planned", table_name="reddit_campaign_actions"
    )
    op.drop_column("reddit_vote_logs", "score_at_vote")
    op.drop_column("reddit_posts", "narrative_angle")
