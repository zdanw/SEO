"""静态加密工具：数据库内敏感字段（第三方凭证等）的对称加密与确定性哈希。

- 加密：Fernet（AES-128-CBC + HMAC-SHA256，随机 IV）——同一明文每次密文不同
- 密钥：优先 ENCRYPTION_KEY 环境变量，未设置则从 SECRET_KEY 派生（sha256）。
  注意：轮换 SECRET_KEY 前必须先固化独立 ENCRYPTION_KEY，否则存量密文不可解
- 哈希：HMAC-SHA256（同一密钥材料作 pepper）——确定性，供唯一约束去重使用
  （Fernet 随机 IV 导致密文列无法做唯一约束，必须另建哈希列）
- 前缀：`enc:v1:` 标记密文；无前缀值视为存量明文原样返回，平滑兼容迁移过渡期
"""
from __future__ import annotations

import base64
import hashlib
import hmac
from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import Text, TypeDecorator

from app.core.config import settings
from app.core.errors import AppException

CIPHER_PREFIX = "enc:v1:"


@lru_cache(maxsize=1)
def _key_material() -> bytes:
    material = (settings.ENCRYPTION_KEY or settings.SECRET_KEY or "").encode("utf-8")
    return hashlib.sha256(material).digest()


@lru_cache(maxsize=1)
def _fernet() -> Fernet:
    return Fernet(base64.urlsafe_b64encode(_key_material()))


def encrypt_value(plain: str) -> str:
    if not plain:
        return plain
    return CIPHER_PREFIX + _fernet().encrypt(plain.encode("utf-8")).decode("ascii")


def decrypt_value(value: str) -> str:
    if not value or not value.startswith(CIPHER_PREFIX):
        return value  # 存量明文：原样返回（迁移平滑兼容）
    try:
        return _fernet().decrypt(value[len(CIPHER_PREFIX) :].encode("ascii")).decode("utf-8")
    except InvalidToken as exc:
        raise AppException(
            "敏感字段解密失败：加密密钥不匹配（SECRET_KEY/ENCRYPTION_KEY 曾被轮换？）",
            code="decrypt_failed",
            status_code=500,
        ) from exc


def compute_key_hash(plain: str) -> str:
    """确定性 HMAC-SHA256（密钥材料作 pepper）：同明文必得同哈希，用于唯一去重。"""
    if not plain:
        return ""
    return hmac.new(_key_material(), plain.encode("utf-8"), hashlib.sha256).hexdigest()


class EncryptedText(TypeDecorator):
    """透明加解密的 Text 列：ORM 写入自动加密、读取自动解密（存量明文直通）。"""

    impl = Text
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return value
        return encrypt_value(value)

    def process_result_value(self, value, dialect):
        if value is None:
            return value
        return decrypt_value(value)
