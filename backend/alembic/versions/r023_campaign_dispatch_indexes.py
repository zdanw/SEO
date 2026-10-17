"""阶段 4 索引调优：执行发放与生成重投扫描的部分索引（CONCURRENTLY，非事务）。

Revision ID: r023_campaign_dispatch_indexes
Revises: r022_action_generation_context
Create Date: 2026-10-17

负载测试（scripts/campaign_load_test.py：1000 campaign / 42k 动作）EXPLAIN 实测：
- due_upvote_action_ids / claim_due_actions_batch：12ms，走 planned_at 全量索引
  且 Rows Removed by Filter ≈ 30k → 本迁移部分索引后精确命中 due 集合
- requeue_stale_generation_actions：11.6ms，同型（Rows Removed by Filter ≈ 31k）
- 其余热查询（配额聚合 / 忙碌区间 / 僵尸回收 / 内容对账）已命中现有索引，
  全程无 Seq Scan——available_at 独立索引不建：发放/重投谓词均为
  (available_at IS NULL OR available_at <= now()) 的 OR 形态，独立列索引
  无法生效，按 planned_at 排序的谓词部分索引才是实测有效形态。

说明：CREATE INDEX CONCURRENTLY 不能在事务块内执行——先 COMMIT 结束 alembic
的迁移事务（r021 文档预留的阶段 4 非事务迁移模式）。索引为部分索引，体积
不随终态历史行增长。
"""
from __future__ import annotations

from alembic import op

revision = "r023_campaign_dispatch_indexes"
down_revision = "r022_action_generation_context"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 结束 alembic 的迁移事务，使 CONCURRENTLY 可用（非事务迁移）
    op.execute("COMMIT")
    op.execute(
        """
        CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_campaign_actions_due_dispatch
        ON reddit_campaign_actions (planned_at)
        WHERE status = 'pending'
          AND action_type IN ('upvote_related', 'upvote_self')
        """
    )
    op.execute(
        """
        CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_campaign_actions_gen_requeue
        ON reddit_campaign_actions (planned_at)
        WHERE status = 'pending'
          AND action_type IN ('post', 'comment')
          AND generation_status IN ('queued', 'generating')
        """
    )


def downgrade() -> None:
    op.execute("COMMIT")
    op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_campaign_actions_gen_requeue")
    op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_campaign_actions_due_dispatch")
