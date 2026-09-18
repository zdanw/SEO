"""Replace product brief with brand/product library; comment brand/product FKs.

Revision ID: r007
Revises: r006
Create Date: 2026-09-18
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "r007_reddit_brand_products"
down_revision = "r006_reddit_smart_ops"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "reddit_brands",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("site_id", sa.Integer(), sa.ForeignKey("client_sites.id"), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_reddit_brands_site_id", "reddit_brands", ["site_id"])

    op.create_table(
        "reddit_products",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("brand_id", sa.Integer(), sa.ForeignKey("reddit_brands.id"), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("category", sa.String(length=120), nullable=False, server_default=""),
        sa.Column("talking_points", sa.JSON(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_reddit_products_brand_id", "reddit_products", ["brand_id"])

    op.add_column("reddit_comments", sa.Column("brand_id", sa.Integer(), nullable=True))
    op.add_column("reddit_comments", sa.Column("product_id", sa.Integer(), nullable=True))
    op.create_index("ix_reddit_comments_brand_id", "reddit_comments", ["brand_id"])
    op.create_index("ix_reddit_comments_product_id", "reddit_comments", ["product_id"])
    op.create_foreign_key(
        "fk_reddit_comments_brand_id",
        "reddit_comments",
        "reddit_brands",
        ["brand_id"],
        ["id"],
    )
    op.create_foreign_key(
        "fk_reddit_comments_product_id",
        "reddit_comments",
        "reddit_products",
        ["product_id"],
        ["id"],
    )

    # Migrate old site briefs → one brand + one product each
    conn = op.get_bind()
    from sqlalchemy import inspect as sa_inspect
    import json as _json

    insp = sa_inspect(conn)
    if "reddit_product_briefs" in insp.get_table_names():
        rows = conn.execute(
            sa.text(
                "SELECT site_id, brand, category, talking_points FROM reddit_product_briefs "
                "WHERE brand IS NOT NULL AND trim(brand) <> ''"
            )
        ).mappings().all()
        for row in rows:
            brand_name = (row["brand"] or "").strip() or "Default"
            product_name = (row["category"] or "").strip() or brand_name
            category = (row["category"] or "").strip()
            points = row["talking_points"]
            if points is not None and not isinstance(points, str):
                points = _json.dumps(points)
            r = conn.execute(
                sa.text(
                    "INSERT INTO reddit_brands (site_id, name, is_active, created_at, updated_at) "
                    "VALUES (:site_id, :name, true, now(), now()) RETURNING id"
                ),
                {"site_id": row["site_id"], "name": brand_name},
            )
            brand_id = r.scalar_one()
            conn.execute(
                sa.text(
                    "INSERT INTO reddit_products "
                    "(brand_id, name, category, talking_points, is_active, created_at, updated_at) "
                    "VALUES (:brand_id, :name, :category, CAST(:points AS json), true, now(), now())"
                ),
                {
                    "brand_id": brand_id,
                    "name": product_name,
                    "category": category,
                    "points": points,
                },
            )
        op.drop_index("ix_reddit_product_briefs_site_id", table_name="reddit_product_briefs")
        op.drop_table("reddit_product_briefs")


def downgrade() -> None:
    op.create_table(
        "reddit_product_briefs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("site_id", sa.Integer(), sa.ForeignKey("client_sites.id"), nullable=False),
        sa.Column("brand", sa.String(length=120), nullable=False, server_default=""),
        sa.Column("category", sa.String(length=120), nullable=False, server_default=""),
        sa.Column("talking_points", sa.JSON(), nullable=True),
        sa.Column("competitors", sa.JSON(), nullable=True),
        sa.Column("never_claim", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_reddit_product_briefs_site_id", "reddit_product_briefs", ["site_id"], unique=True)

    op.drop_constraint("fk_reddit_comments_product_id", "reddit_comments", type_="foreignkey")
    op.drop_constraint("fk_reddit_comments_brand_id", "reddit_comments", type_="foreignkey")
    op.drop_index("ix_reddit_comments_product_id", table_name="reddit_comments")
    op.drop_index("ix_reddit_comments_brand_id", table_name="reddit_comments")
    op.drop_column("reddit_comments", "product_id")
    op.drop_column("reddit_comments", "brand_id")
    op.drop_index("ix_reddit_products_brand_id", table_name="reddit_products")
    op.drop_table("reddit_products")
    op.drop_index("ix_reddit_brands_site_id", table_name="reddit_brands")
    op.drop_table("reddit_brands")
