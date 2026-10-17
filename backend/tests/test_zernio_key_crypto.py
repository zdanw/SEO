"""Zernio API Key 静态加密单元测试。"""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from app.core.crypto import (
    CIPHER_PREFIX,
    compute_key_hash,
    decrypt_value,
    encrypt_value,
)
from app.core.database import Base
from app.models.zernio_key import ZernioApiKey


@pytest.fixture()
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    session = sessionmaker(bind=engine, autoflush=False)()
    Base.metadata.create_all(engine, tables=[ZernioApiKey.__table__])
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


# ============ 加解密原语 ============
def test_encrypt_decrypt_roundtrip():
    cipher = encrypt_value("sk_live_abc123")
    assert cipher.startswith(CIPHER_PREFIX)
    assert cipher != "sk_live_abc123"
    assert decrypt_value(cipher) == "sk_live_abc123"


def test_decrypt_passthrough_legacy_plaintext():
    """无 enc:v1 前缀的存量明文原样返回（迁移平滑兼容）。"""
    assert decrypt_value("sk_legacy_plain") == "sk_legacy_plain"
    assert decrypt_value("") == ""


def test_same_plaintext_yields_different_ciphertexts():
    """Fernet 随机 IV：同明文两次加密密文不同——因此去重必须走哈希列。"""
    assert encrypt_value("sk_x") != encrypt_value("sk_x")


def test_key_hash_deterministic():
    assert compute_key_hash("sk_a") == compute_key_hash("sk_a")
    assert compute_key_hash("sk_a") != compute_key_hash("sk_b")
    assert len(compute_key_hash("sk_a")) == 64


# ============ ORM 透明加解密 ============
def test_orm_writes_ciphertext_and_hash(db):
    """ORM 写入落库为密文 + 哈希自动回填；读回自动解密为明文。"""
    row = ZernioApiKey(site_id=1, label="L", api_key="sk_live_orm_key", is_enabled=True)
    db.add(row)
    db.commit()

    raw = db.execute(
        text("SELECT api_key, api_key_hash FROM zernio_api_keys WHERE id = :id"),
        {"id": row.id},
    ).fetchone()
    assert raw.api_key.startswith(CIPHER_PREFIX)  # 库内是密文
    assert raw.api_key_hash == compute_key_hash("sk_live_orm_key")

    fresh = db.query(ZernioApiKey).filter_by(id=row.id).one()
    assert fresh.api_key == "sk_live_orm_key"  # 读回自动解密


def test_duplicate_key_same_site_rejected(db):
    """同站点同 Key 去重：唯一约束落在 (site_id, api_key_hash)。"""
    db.add(ZernioApiKey(site_id=1, label="A", api_key="sk_dup", is_enabled=True))
    db.commit()
    db.add(ZernioApiKey(site_id=1, label="B", api_key="sk_dup", is_enabled=True))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_same_key_different_sites_allowed(db):
    db.add(ZernioApiKey(site_id=1, label="A", api_key="sk_shared", is_enabled=True))
    db.add(ZernioApiKey(site_id=2, label="B", api_key="sk_shared", is_enabled=True))
    db.commit()
    assert db.query(ZernioApiKey).count() == 2


def test_update_key_rehashes(db):
    """PATCH 更新 Key：哈希跟随新明文，旧哈希不残留。"""
    row = ZernioApiKey(site_id=1, label="A", api_key="sk_old", is_enabled=True)
    db.add(row)
    db.commit()
    old_hash = row.api_key_hash

    row.api_key = "sk_new"
    db.commit()
    assert row.api_key_hash != old_hash
    assert row.api_key_hash == compute_key_hash("sk_new")
    assert row.api_key == "sk_new"
