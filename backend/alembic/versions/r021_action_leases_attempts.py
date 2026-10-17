"""阶段 1 状态正确性：动作租约 / fencing 代次 / attempts 审计表 / config_version。

Revision ID: r021_action_leases_attempts
Revises: r020_zernio_key_encryption
Create Date: 2026-10-17

RedditCampaignAction（reddit_campaign_actions）增列：
- lease_owner：执行租约持有者（worker 身份，仅供观测；终态校验以代次为准）
- execution_lease_generation / generation_lease_generation：两阶段独立 fencing 代次，
  各自领取时 +1，续租与终态回写必须校验所属阶段代次（旧 worker 晚到不可覆盖）
- lease_until：执行租约到期时刻（僵尸回收按此判定，替代 updated_at 时间推断）
- available_at：投递可用时刻（重试退避 / 到期调度，替代 planned_at 复用）
- generation_status：生成生命周期（not_required / queued / generating / ready / failed；
  阶段 1 先建列并默认 not_required，阶段 2 切换启用）
- generation_attempt_count / execution_attempt_count：按阶段独立尝试计数
- generation_max_attempts / execution_max_attempts：按阶段独立重试预算
- result_status：外部结果分类（confirmed_success / confirmed_absent / unknown 等）
- last_error_code：错误分类码（retryable_rate_limit / timeout / api_error / ...）
- cancel_requested_at：取消/暂停协议的取消请求时刻

reddit_campaign_action_attempts 新表（每次尝试一条审计记录，两段持久化）：
- phase 区分 generation / execution；UNIQUE(action_id, phase, attempt_no)
- request_state：request_pending（领取事务预写，请求尚未发出）→ responded（终态事务回写）
- 请求开始时间 / worker 身份 / 错误分类 / 外部对象 ID / 结果摘要

reddit_campaigns 增列 config_version（配置修改递增；动作规划时快照，执行前校验）。

说明：本迁移在事务内执行；attempts 表与现有表规模（当前千级行数）下普通
CREATE INDEX 足够，CREATE INDEX CONCURRENTLY 留给阶段 4 大表调优（届时需非事务迁移）。
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "r021_action_leases_attempts"
down_revision = "r020_zernio_key_encryption"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ---- reddit_campaigns: config_version ----
    op.add_column(
        "reddit_campaigns",
        sa.Column("config_version", sa.Integer(), nullable=False, server_default="1"),
    )

    # ---- reddit_campaign_actions: 租约 / 代次 / 计数 / 协议列 ----
    # 规划时快照，执行前与 campaign.config_version 比对做配置失效分级
    op.add_column(
        "reddit_campaign_actions",
        sa.Column("config_version", sa.Integer(), nullable=False, server_default="1"),
    )
    op.add_column(
        "reddit_campaign_actions",
        sa.Column("lease_owner", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "reddit_campaign_actions",
        sa.Column(
            "execution_lease_generation", sa.Integer(), nullable=False, server_default="0"
        ),
    )
    op.add_column(
        "reddit_campaign_actions",
        sa.Column(
            "generation_lease_generation", sa.Integer(), nullable=False, server_default="0"
        ),
    )
    op.add_column(
        "reddit_campaign_actions",
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "reddit_campaign_actions",
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "reddit_campaign_actions",
        sa.Column(
            "generation_status", sa.String(length=20), nullable=False, server_default="not_required"
        ),
    )
    op.add_column(
        "reddit_campaign_actions",
        sa.Column(
            "generation_attempt_count", sa.Integer(), nullable=False, server_default="0"
        ),
    )
    op.add_column(
        "reddit_campaign_actions",
        sa.Column(
            "execution_attempt_count", sa.Integer(), nullable=False, server_default="0"
        ),
    )
    op.add_column(
        "reddit_campaign_actions",
        sa.Column("generation_max_attempts", sa.Integer(), nullable=False, server_default="2"),
    )
    op.add_column(
        "reddit_campaign_actions",
        sa.Column("execution_max_attempts", sa.Integer(), nullable=False, server_default="2"),
    )
    op.add_column(
        "reddit_campaign_actions",
        sa.Column("result_status", sa.String(length=30), nullable=True),
    )
    op.add_column(
        "reddit_campaign_actions",
        sa.Column("last_error_code", sa.String(length=50), nullable=True),
    )
    op.add_column(
        "reddit_campaign_actions",
        sa.Column("cancel_requested_at", sa.DateTime(timezone=True), nullable=True),
    )
    # 僵尸回收 / 重投扫描索引：仅扫执行中的行（部分索引，行数随 executed 增长不变）
    op.create_index(
        "ix_campaign_actions_status_lease_until",
        "reddit_campaign_actions",
        ["status", "lease_until"],
        postgresql_where=sa.text("status IN ('executing', 'reconciling')"),
    )

    # ---- reddit_campaign_action_attempts 审计表 ----
    op.create_table(
        "reddit_campaign_action_attempts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "action_id",
            sa.Integer(),
            sa.ForeignKey("reddit_campaign_actions.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        # generation / execution
        sa.Column("phase", sa.String(length=20), nullable=False),
        # 该动作该阶段内的序号（1 起按次递增）
        sa.Column("attempt_no", sa.Integer(), nullable=False),
        # 两段持久化：request_pending（预写，请求尚未发出）→ responded（终态回写）
        sa.Column("request_state", sa.String(length=20), nullable=False),
        sa.Column("worker_id", sa.String(length=64), nullable=True),
        sa.Column("request_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("responded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.String(length=50), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        # 外部对象 ID（thing_id / 外部请求标识，供对账直查核实）
        sa.Column("external_object_id", sa.String(length=100), nullable=True),
        sa.Column("result_summary", sa.JSON(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint(
            "action_id", "phase", "attempt_no", name="uq_campaign_action_attempt"
        ),
        sa.CheckConstraint(
            "phase IN ('generation', 'execution')", name="ck_attempt_phase"
        ),
        sa.CheckConstraint(
            "request_state IN ('request_pending', 'responded')",
            name="ck_attempt_request_state",
        ),
    )
    op.create_index(
        "ix_campaign_attempts_action_phase",
        "reddit_campaign_action_attempts",
        ["action_id", "phase"],
    )


def downgrade() -> None:
    op.drop_table("reddit_campaign_action_attempts")
    op.drop_index(
        "ix_campaign_actions_status_lease_until", table_name="reddit_campaign_actions"
    )
    for col in (
        "config_version",
        "cancel_requested_at",
        "last_error_code",
        "result_status",
        "execution_max_attempts",
        "generation_max_attempts",
        "execution_attempt_count",
        "generation_attempt_count",
        "generation_status",
        "available_at",
        "lease_until",
        "generation_lease_generation",
        "execution_lease_generation",
        "lease_owner",
    ):
        op.drop_column("reddit_campaign_actions", col)
    op.drop_column("reddit_campaigns", "config_version")
