"""P4b: gsc_connections oauth pending fields

Revision ID: f2b3c4d5e6f7
Revises: f1a2b3c4d5e6
Create Date: 2026-09-01 11:45:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f2b3c4d5e6f7"
down_revision: Union[str, None] = "f1a2b3c4d5e6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("gsc_connections", sa.Column("oauth_code_verifier", sa.Text(), nullable=True))
    op.add_column("gsc_connections", sa.Column("oauth_state_nonce", sa.String(length=64), nullable=True))


def downgrade() -> None:
    op.drop_column("gsc_connections", "oauth_state_nonce")
    op.drop_column("gsc_connections", "oauth_code_verifier")
