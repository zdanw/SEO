"""Placeholder so local DBs already stamped at r001 can upgrade.

The original opportunity-engine revision was applied to this environment
but is not in the current git tree. This file only preserves the revision id.
"""
from typing import Sequence, Union


revision: str = "r001_opportunity_engine"
down_revision: Union[str, None] = "g1h2i3j4k5l6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
