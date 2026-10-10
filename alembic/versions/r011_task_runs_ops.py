"""Product ops: task runs, queue depth, cost snapshot.

Revision ID: r011
Revises: r010_product_communities
Create Date: 2026-09-21
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "r011_task_runs_ops"
down_revision = "r010_product_communities"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "task_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("task_name", sa.String(length=120), nullable=False),
        sa.Column("celery_task_id", sa.String(length=64), nullable=True),
        sa.Column("request_id", sa.String(length=64), nullable=True),
        sa.Column("site_id", sa.Integer(), nullable=True),
        sa.Column("business_type", sa.String(length=40), nullable=True),
        sa.Column("business_id", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="started"),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("retries", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("meta", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.create_index("ix_task_runs_task_name", "task_runs", ["task_name"])
    op.create_index("ix_task_runs_celery_task_id", "task_runs", ["celery_task_id"])
    op.create_index("ix_task_runs_request_id", "task_runs", ["request_id"])
    op.create_index("ix_task_runs_site_id", "task_runs", ["site_id"])
    op.create_index("ix_task_runs_business_type", "task_runs", ["business_type"])
    op.create_index("ix_task_runs_business_id", "task_runs", ["business_id"])
    op.create_index("ix_task_runs_status", "task_runs", ["status"])
    op.create_index("ix_task_runs_started_at", "task_runs", ["started_at"])


def downgrade() -> None:
    op.drop_index("ix_task_runs_started_at", table_name="task_runs")
    op.drop_index("ix_task_runs_status", table_name="task_runs")
    op.drop_index("ix_task_runs_business_id", table_name="task_runs")
    op.drop_index("ix_task_runs_business_type", table_name="task_runs")
    op.drop_index("ix_task_runs_site_id", table_name="task_runs")
    op.drop_index("ix_task_runs_request_id", table_name="task_runs")
    op.drop_index("ix_task_runs_celery_task_id", table_name="task_runs")
    op.drop_index("ix_task_runs_task_name", table_name="task_runs")
    op.drop_table("task_runs")
