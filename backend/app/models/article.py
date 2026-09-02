from datetime import datetime
from decimal import Decimal
from sqlalchemy import Integer, String, DateTime, ForeignKey, Text, Numeric, Boolean, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class Article(Base):
    __tablename__ = "articles"
    __table_args__ = (
        UniqueConstraint("site_id", "slug", name="uq_articles_site_slug"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    site_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("client_sites.id"), nullable=True, index=True
    )
    keyword_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("keywords.id"), nullable=True, index=True
    )

    title: Mapped[str] = mapped_column(String(300), nullable=False)
    meta_description: Mapped[str | None] = mapped_column(String(320), nullable=True)
    slug: Mapped[str | None] = mapped_column(String(300), nullable=True)
    # internal=平台创作 / external=客户站已有页面
    source: Mapped[str] = mapped_column(String(20), default="internal", nullable=False)
    source_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    cms_type: Mapped[str | None] = mapped_column(String(30), nullable=True)
    cms_post_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    sync_status: Mapped[str] = mapped_column(String(20), default="none", nullable=False)
    content: Mapped[str | None] = mapped_column(Text, nullable=True)
    cover_image_url: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # 状态机：draft -> ai_generated -> reviewed -> published
    status: Mapped[str] = mapped_column(String(20), default="draft", nullable=False, index=True)

    # SEO 评分 0-100
    seo_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    # AI 检测得分 0-100（越高越像 AI 生成）
    ai_detected_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    # SEO 明细 JSON：{"title": {"score": 10, "max": 10, "msg": "..."}, ...}
    seo_detail: Mapped[str | None] = mapped_column(Text, nullable=True)

    target_url: Mapped[str | None] = mapped_column(String(500), nullable=True)

    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
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

    keyword: Mapped["Keyword"] = relationship(back_populates="articles")
    internal_links: Mapped[list["InternalLink"]] = relationship(
        back_populates="source_article",
        foreign_keys="InternalLink.source_article_id",
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Article #{self.id} {self.title!r} [{self.status}]>"


class InternalLink(Base):
    """AI 推荐的站内内链记录。"""
    __tablename__ = "internal_links"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_article_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("articles.id"), nullable=False, index=True
    )
    target_article_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("articles.id"), nullable=False, index=True
    )
    anchor_text: Mapped[str] = mapped_column(String(200), nullable=False)
    relevance_score: Mapped[Decimal | None] = mapped_column(Numeric(4, 3), nullable=True)
    is_applied: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=datetime.utcnow,
        nullable=False,
    )

    source_article: Mapped["Article"] = relationship(
        back_populates="internal_links",
        foreign_keys=[source_article_id],
    )
