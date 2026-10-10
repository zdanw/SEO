"""Add site_id to zernio_api_keys for per-site isolation.

Revision ID: r015
Revises: r014_community_rules
Create Date: 2026-09-22
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "r015_zernio_key_site_id"
down_revision = "r014_community_rules"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("zernio_api_keys", sa.Column("site_id", sa.Integer(), nullable=True))

    conn = op.get_bind()
    default_site = conn.execute(
        sa.text(
            "SELECT id FROM client_sites ORDER BY created_at ASC NULLS LAST, id ASC LIMIT 1"
        )
    ).scalar()
    if default_site is not None:
        conn.execute(
            sa.text("UPDATE zernio_api_keys SET site_id = :sid WHERE site_id IS NULL"),
            {"sid": default_site},
        )
    else:
        # No sites yet: drop orphan keys so NOT NULL can apply
        conn.execute(sa.text("DELETE FROM zernio_api_keys WHERE site_id IS NULL"))

    op.alter_column("zernio_api_keys", "site_id", nullable=False)
    op.create_foreign_key(
        "fk_zernio_api_keys_site_id",
        "zernio_api_keys",
        "client_sites",
        ["site_id"],
        ["id"],
    )
    op.create_index("ix_zernio_api_keys_site_id", "zernio_api_keys", ["site_id"])

    # Drop global unique on api_key (name may be auto-generated)
    insp = sa.inspect(conn)
    for uc in insp.get_unique_constraints("zernio_api_keys"):
        cols = uc.get("column_names") or []
        if cols == ["api_key"] or set(cols) == {"api_key"}:
            op.drop_constraint(uc["name"], "zernio_api_keys", type_="unique")
            break
    else:
        # PostgreSQL may also expose unique as an index
        for ix in insp.get_indexes("zernio_api_keys"):
            if ix.get("unique") and (ix.get("column_names") or []) == ["api_key"]:
                op.drop_index(ix["name"], table_name="zernio_api_keys")
                break

    op.create_unique_constraint(
        "uq_zernio_api_keys_site_api_key",
        "zernio_api_keys",
        ["site_id", "api_key"],
    )


def downgrade() -> None:
    op.drop_constraint("uq_zernio_api_keys_site_api_key", "zernio_api_keys", type_="unique")
    op.drop_index("ix_zernio_api_keys_site_id", table_name="zernio_api_keys")
    op.drop_constraint("fk_zernio_api_keys_site_id", "zernio_api_keys", type_="foreignkey")
    op.drop_column("zernio_api_keys", "site_id")
    op.create_unique_constraint("zernio_api_keys_api_key_key", "zernio_api_keys", ["api_key"])
