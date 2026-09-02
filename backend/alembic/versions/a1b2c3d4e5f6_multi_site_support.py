"""多客户站点支持：ClientSite、SiteMember、site_id 字段、SEO 审计表。"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, None] = "f3c4d5e6f7a8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # users.is_admin
    op.add_column("users", sa.Column("is_admin", sa.Boolean(), nullable=False, server_default="false"))

    # client_sites
    op.create_table(
        "client_sites",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("owner_user_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("domain", sa.String(length=200), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="active"),
        sa.Column("industry", sa.String(length=100), nullable=True),
        sa.Column("cms_type", sa.String(length=30), nullable=False, server_default="none"),
        sa.Column("cms_api_url", sa.String(length=500), nullable=True),
        sa.Column("cms_api_key", sa.Text(), nullable=True),
        sa.Column("sitemap_url", sa.String(length=500), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_client_sites_domain"), "client_sites", ["domain"], unique=False)
    op.create_index(op.f("ix_client_sites_owner_user_id"), "client_sites", ["owner_user_id"], unique=False)
    op.create_index(op.f("ix_client_sites_status"), "client_sites", ["status"], unique=False)

    # site_members
    op.create_table(
        "site_members",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("site_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(length=20), nullable=False, server_default="operator"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["site_id"], ["client_sites.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("site_id", "user_id", name="uq_site_members_site_user"),
    )
    op.create_index(op.f("ix_site_members_site_id"), "site_members", ["site_id"], unique=False)
    op.create_index(op.f("ix_site_members_user_id"), "site_members", ["user_id"], unique=False)

    # seo_audits
    op.create_table(
        "seo_audits",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("site_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="pending"),
        sa.Column("total_pages", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("scanned_pages", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("avg_score", sa.Numeric(precision=5, scale=2), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["site_id"], ["client_sites.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_seo_audits_site_id"), "seo_audits", ["site_id"], unique=False)
    op.create_index(op.f("ix_seo_audits_status"), "seo_audits", ["status"], unique=False)

    # seo_audit_pages
    op.create_table(
        "seo_audit_pages",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("audit_id", sa.Integer(), nullable=False),
        sa.Column("url", sa.String(length=500), nullable=False),
        sa.Column("title", sa.String(length=300), nullable=True),
        sa.Column("score", sa.Numeric(precision=5, scale=2), nullable=True),
        sa.Column("issues_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("detail_json", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["audit_id"], ["seo_audits.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_seo_audit_pages_audit_id"), "seo_audit_pages", ["audit_id"], unique=False)

    # site_id on business tables
    for table in ("keywords", "articles", "backlinks", "competitors", "recommendations", "social_accounts"):
        op.add_column(table, sa.Column("site_id", sa.Integer(), nullable=True))
        op.create_index(f"ix_{table}_site_id", table, ["site_id"], unique=False)
        op.create_foreign_key(f"fk_{table}_site_id", table, "client_sites", ["site_id"], ["id"])

    # article external/cms fields
    op.add_column("articles", sa.Column("source", sa.String(length=20), nullable=False, server_default="internal"))
    op.add_column("articles", sa.Column("source_url", sa.String(length=500), nullable=True))
    op.add_column("articles", sa.Column("cms_type", sa.String(length=30), nullable=True))
    op.add_column("articles", sa.Column("cms_post_id", sa.String(length=100), nullable=True))
    op.add_column("articles", sa.Column("sync_status", sa.String(length=20), nullable=False, server_default="none"))

    # drop global slug unique, add per-site unique
    op.drop_constraint("articles_slug_key", "articles", type_="unique")
    op.create_unique_constraint("uq_articles_site_slug", "articles", ["site_id", "slug"])

    # competitors: change unique constraint
    op.drop_constraint("uq_competitors_user_domain", "competitors", type_="unique")
    op.create_unique_constraint("uq_competitors_site_domain", "competitors", ["site_id", "domain"])

    # gsc_connections: add site_id, drop user_id unique
    op.add_column("gsc_connections", sa.Column("site_id", sa.Integer(), nullable=True))
    op.drop_index("ix_gsc_connections_user_id", table_name="gsc_connections")
    op.create_index(op.f("ix_gsc_connections_user_id"), "gsc_connections", ["user_id"], unique=False)
    op.create_index(op.f("ix_gsc_connections_site_id"), "gsc_connections", ["site_id"], unique=True)
    op.create_foreign_key("fk_gsc_connections_site_id", "gsc_connections", "client_sites", ["site_id"], ["id"])
    op.drop_constraint("gsc_connections_user_id_key", "gsc_connections", type_="unique")

    # data migration: create default site per user and assign site_id
    conn = op.get_bind()
    users = conn.execute(sa.text("SELECT id FROM users")).fetchall()
    for (user_id,) in users:
        now = sa.text("NOW()")
        result = conn.execute(
            sa.text(
                """
                INSERT INTO client_sites (owner_user_id, name, domain, status, cms_type, created_at, updated_at)
                VALUES (:uid, '默认客户站点', 'example.com', 'active', 'none', NOW(), NOW())
                RETURNING id
                """
            ),
            {"uid": user_id},
        )
        site_id = result.scalar_one()
        conn.execute(
            sa.text(
                """
                INSERT INTO site_members (site_id, user_id, role, created_at)
                VALUES (:sid, :uid, 'admin', NOW())
                """
            ),
            {"sid": site_id, "uid": user_id},
        )
        for table in ("keywords", "articles", "backlinks", "competitors", "recommendations", "social_accounts"):
            conn.execute(
                sa.text(f"UPDATE {table} SET site_id = :sid WHERE user_id = :uid"),
                {"sid": site_id, "uid": user_id},
            )
        conn.execute(
            sa.text("UPDATE gsc_connections SET site_id = :sid WHERE user_id = :uid"),
            {"sid": site_id, "uid": user_id},
        )


def downgrade() -> None:
    op.drop_constraint("fk_gsc_connections_site_id", "gsc_connections", type_="foreignkey")
    op.drop_index(op.f("ix_gsc_connections_site_id"), table_name="gsc_connections")
    op.drop_column("gsc_connections", "site_id")
    op.create_unique_constraint("gsc_connections_user_id_key", "gsc_connections", ["user_id"])

    op.drop_constraint("uq_competitors_site_domain", "competitors", type_="unique")
    op.create_unique_constraint("uq_competitors_user_domain", "competitors", ["user_id", "domain"])

    op.drop_constraint("uq_articles_site_slug", "articles", type_="unique")
    op.create_unique_constraint("articles_slug_key", "articles", ["slug"])

    for col in ("sync_status", "cms_post_id", "cms_type", "source_url", "source"):
        op.drop_column("articles", col)

    for table in ("social_accounts", "recommendations", "competitors", "backlinks", "articles", "keywords"):
        op.drop_constraint(f"fk_{table}_site_id", table, type_="foreignkey")
        op.drop_index(f"ix_{table}_site_id", table_name=table)
        op.drop_column(table, "site_id")

    op.drop_index(op.f("ix_seo_audit_pages_audit_id"), table_name="seo_audit_pages")
    op.drop_table("seo_audit_pages")
    op.drop_index(op.f("ix_seo_audits_status"), table_name="seo_audits")
    op.drop_index(op.f("ix_seo_audits_site_id"), table_name="seo_audits")
    op.drop_table("seo_audits")
    op.drop_index(op.f("ix_site_members_user_id"), table_name="site_members")
    op.drop_index(op.f("ix_site_members_site_id"), table_name="site_members")
    op.drop_table("site_members")
    op.drop_index(op.f("ix_client_sites_status"), table_name="client_sites")
    op.drop_index(op.f("ix_client_sites_owner_user_id"), table_name="client_sites")
    op.drop_index(op.f("ix_client_sites_domain"), table_name="client_sites")
    op.drop_table("client_sites")
    op.drop_column("users", "is_admin")
