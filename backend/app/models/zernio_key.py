from datetime import datetime
from app.core.timeutil import now_bj

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, validates

from app.core.crypto import EncryptedText, compute_key_hash
from app.core.database import Base


class ZernioApiKey(Base):
    """按站点隔离的 Zernio API Key（api_key 落库自动加密，api_key_hash 承接去重）。"""

    __tablename__ = "zernio_api_keys"
    __table_args__ = (
        # Fernet 随机 IV 下密文列无去重意义：唯一约束用确定性哈希列承接
        UniqueConstraint("site_id", "api_key_hash", name="uq_zernio_api_keys_site_hash"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    site_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("client_sites.id"), nullable=False, index=True
    )
    label: Mapped[str] = mapped_column(String(100), nullable=False)
    api_key: Mapped[str] = mapped_column(EncryptedText(), nullable=False)
    api_key_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    profile_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=now_bj,
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=now_bj,
        onupdate=now_bj,
        nullable=False,
    )

    @validates("api_key")
    def _sync_api_key_hash(self, key: str, value: str | None) -> str | None:
        """api_key 赋值（构造/更新）时自动同步确定性哈希，端点层零感知。"""
        self.api_key_hash = compute_key_hash(value or "")
        return value

    def __repr__(self) -> str:  # pragma: no cover
        return f"<ZernioApiKey #{self.id} site={self.site_id} {self.label}>"
