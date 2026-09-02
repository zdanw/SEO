"""Drop crawler_logs table (module removed in favor of Search Console).

Revision ID: f3c4d5e6f7a8
Revises: f2b3c4d5e6f7
Create Date: 2026-09-01 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op


revision: str = "f3c4d5e6f7a8"
down_revision: Union[str, None] = "f2b3c4d5e6f7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_index("ix_crawler_logs_crawled_at", table_name="crawler_logs")
    op.drop_index("ix_crawler_logs_crawler_name", table_name="crawler_logs")
    op.drop_index("ix_crawler_logs_user_id", table_name="crawler_logs")
    op.drop_table("crawler_logs")


def downgrade() -> None:
    import sqlalchemy as sa

    op.create_table(
        "crawler_logs",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("url_path", sa.String(length=500), nullable=False),
        sa.Column("crawler_name", sa.String(length=50), nullable=False),
        sa.Column("status_code", sa.Integer(), nullable=False),
        sa.Column("user_agent", sa.Text(), nullable=True),
        sa.Column("ip", sa.String(length=45), nullable=True),
        sa.Column("crawled_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_crawler_logs_user_id", "crawler_logs", ["user_id"], unique=False)
    op.create_index("ix_crawler_logs_crawler_name", "crawler_logs", ["crawler_name"], unique=False)
    op.create_index("ix_crawler_logs_crawled_at", "crawler_logs", ["crawled_at"], unique=False)
