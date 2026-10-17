"""Zernio API Key 静态加密：api_key 加密落库 + 确定性哈希列承接唯一约束。

Revision ID: r020_zernio_key_encryption
Revises: r019_content_feedback
Create Date: 2026-10-16

- zernio_api_keys 新增 api_key_hash（HMAC-SHA256 确定性哈希，回填后置 NOT NULL）
- 存量明文 api_key 逐行加密（enc:v1: 前缀，重跑幂等）
- 原唯一约束 uq_zernio_api_keys_site_api_key 移除（Fernet 随机 IV 下密文无去重意义），
  由 unique(site_id, api_key_hash) 承接去重语义
- downgrade：解密还原明文、删哈希列、恢复旧唯一约束
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from app.core.crypto import compute_key_hash, decrypt_value, encrypt_value

revision = "r020_zernio_key_encryption"
down_revision = "r019_content_feedback"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "zernio_api_keys",
        sa.Column("api_key_hash", sa.String(length=64), nullable=True),
    )
    conn = op.get_bind()
    rows = conn.execute(sa.text("SELECT id, api_key FROM zernio_api_keys")).fetchall()
    for row_id, api_key in rows:
        plain = decrypt_value(api_key or "")  # 存量明文直通；已有 enc:v1 前缀的解密
        conn.execute(
            sa.text("UPDATE zernio_api_keys SET api_key = :ak, api_key_hash = :h WHERE id = :id"),
            {"ak": encrypt_value(plain), "h": compute_key_hash(plain), "id": row_id},
        )
    op.alter_column(
        "zernio_api_keys",
        "api_key_hash",
        existing_type=sa.String(length=64),
        nullable=False,
    )
    op.drop_constraint("uq_zernio_api_keys_site_api_key", "zernio_api_keys", type_="unique")
    op.create_unique_constraint(
        "uq_zernio_api_keys_site_hash", "zernio_api_keys", ["site_id", "api_key_hash"]
    )


def downgrade() -> None:
    conn = op.get_bind()
    rows = conn.execute(sa.text("SELECT id, api_key FROM zernio_api_keys")).fetchall()
    for row_id, api_key in rows:
        plain = decrypt_value(api_key or "")
        conn.execute(
            sa.text("UPDATE zernio_api_keys SET api_key = :ak WHERE id = :id"),
            {"ak": plain, "id": row_id},
        )
    op.drop_constraint("uq_zernio_api_keys_site_hash", "zernio_api_keys", type_="unique")
    op.create_unique_constraint(
        "uq_zernio_api_keys_site_api_key", "zernio_api_keys", ["site_id", "api_key"]
    )
    op.drop_column("zernio_api_keys", "api_key_hash")
