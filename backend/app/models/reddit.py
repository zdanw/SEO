from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class RedditPost(Base):
    """Reddit 发帖审核队列。"""

    __tablename__ = "reddit_posts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    site_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("client_sites.id"), nullable=False, index=True
    )
    account_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("social_accounts.id"), nullable=False, index=True
    )
    post_type: Mapped[str] = mapped_column(String(20), nullable=False)  # consultation / experience
    subreddit: Mapped[str] = mapped_column(String(100), nullable=False)
    keyword: Mapped[str] = mapped_column(String(200), nullable=False)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    site_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="draft", nullable=False, index=True)
    reddit_post_id: Mapped[str | None] = mapped_column(String(50), nullable=True)
    reddit_permalink: Mapped[str | None] = mapped_column(String(500), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    account: Mapped["SocialAccount"] = relationship("SocialAccount")


class RedditComment(Base):
    """Reddit 评论审核队列。"""

    __tablename__ = "reddit_comments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    site_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("client_sites.id"), nullable=False, index=True
    )
    account_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("social_accounts.id"), nullable=False, index=True
    )
    target_post_url: Mapped[str] = mapped_column(String(500), nullable=False)
    target_thing_id: Mapped[str] = mapped_column(String(20), nullable=False)
    subreddit: Mapped[str] = mapped_column(String(100), nullable=False)
    target_post_title: Mapped[str | None] = mapped_column(String(500), nullable=True)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    keyword: Mapped[str | None] = mapped_column(String(200), nullable=True)
    discover_source: Mapped[str] = mapped_column(String(20), default="manual_url", nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="draft", nullable=False, index=True)
    reddit_comment_id: Mapped[str | None] = mapped_column(String(50), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    account: Mapped["SocialAccount"] = relationship("SocialAccount")


from app.models.social import SocialAccount  # noqa: E402
