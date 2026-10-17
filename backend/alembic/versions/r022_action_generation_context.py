"""阶段 2（tick 拆分）：动作表加 generation_context 上下文快照列。

评论骨架动作把发现候选（url/thing_id/title/body/subreddit/keyword）随行持久化，
generation 队列任务生成时免重复抓取帖子内容；帖子上下文全可由 account+campaign
重推导，该列留空。

Revision ID: r022_action_generation_context
Revises: r021_action_leases_attempts
Create Date: 2026-10-17
"""

from alembic import op
import sqlalchemy as sa

revision = "r022_action_generation_context"
down_revision = "r021_action_leases_attempts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "reddit_campaign_actions",
        sa.Column("generation_context", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("reddit_campaign_actions", "generation_context")
