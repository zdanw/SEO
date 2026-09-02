from datetime import datetime
from sqlalchemy import Integer, String, DateTime, ForeignKey, Text, Boolean, JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class SocialAccount(Base):
    """社交平台账号配置。"""
    __tablename__ = "social_accounts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    site_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("client_sites.id"), nullable=True, index=True
    )
    platform: Mapped[str] = mapped_column(String(50), nullable=False)  # pulseforge / linkedin / twitter / facebook
    account_name: Mapped[str] = mapped_column(String(100), nullable=False)
    access_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    refresh_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    token_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    config: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # 平台特定配置
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=datetime.utcnow,
        nullable=False,
    )

    posts: Mapped[list["SocialPost"]] = relationship(back_populates="account")

    def __repr__(self) -> str:  # pragma: no cover
        return f"<SocialAccount #{self.id} {self.platform}:{self.account_name}>"


class SocialPost(Base):
    """社交发帖任务。"""
    __tablename__ = "social_posts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    article_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("articles.id"), nullable=True, index=True
    )
    account_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("social_accounts.id"), nullable=False, index=True
    )

    title: Mapped[str | None] = mapped_column(String(300), nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    image_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    external_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    hashtags: Mapped[list | None] = mapped_column(JSON, nullable=True)

    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    posted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    platform_post_id: Mapped[str | None] = mapped_column(String(200), nullable=True)

    # pending / scheduled / posting / posted / failed / cancelled
    status: Mapped[str] = mapped_column(String(20), default="pending", nullable=False, index=True)
    engagement: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=datetime.utcnow,
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )

    account: Mapped["SocialAccount"] = relationship(back_populates="posts")

    def __repr__(self) -> str:  # pragma: no cover
        return f"<SocialPost #{self.id} {self.status} account={self.account_id}>"
