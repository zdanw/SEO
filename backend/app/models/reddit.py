from datetime import datetime

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    JSON,
    String,
    Table,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


reddit_product_communities = Table(
    "reddit_product_communities",
    Base.metadata,
    Column("product_id", Integer, ForeignKey("reddit_products.id", ondelete="CASCADE"), primary_key=True),
    Column("community_id", Integer, ForeignKey("reddit_communities.id", ondelete="CASCADE"), primary_key=True),
)


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
    post_type: Mapped[str] = mapped_column(String(20), nullable=False)  # auto/pitfall/vent/unpopular/guide/help_seek
    subreddit: Mapped[str] = mapped_column(String(100), nullable=False)
    keyword: Mapped[str] = mapped_column(String(200), nullable=False)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    site_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="draft", nullable=False, index=True)
    reddit_post_id: Mapped[str | None] = mapped_column(String(50), nullable=True)
    reddit_permalink: Mapped[str | None] = mapped_column(String(500), nullable=True)
    scheduled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    content_intent: Mapped[str] = mapped_column(String(20), default="casual", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    account: Mapped["SocialAccount"] = relationship("SocialAccount")
    metrics: Mapped[list["RedditPostMetric"]] = relationship(
        "RedditPostMetric", back_populates="post", cascade="all, delete-orphan"
    )


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
    content_intent: Mapped[str] = mapped_column(String(20), default="casual", nullable=False)
    ai_risk: Mapped[str | None] = mapped_column(String(20), nullable=True)
    brand_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("reddit_brands.id"), nullable=True, index=True
    )
    product_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("reddit_products.id"), nullable=True, index=True
    )
    status: Mapped[str] = mapped_column(String(20), default="draft", nullable=False, index=True)
    reddit_comment_id: Mapped[str | None] = mapped_column(String(50), nullable=True)
    scheduled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    account: Mapped["SocialAccount"] = relationship("SocialAccount")


class RedditAccountProfile(Base):
    """Reddit 账号矩阵档案：养号号 / 种草号 / 答疑号 分层运营。

    对应方案 第三章-1「账号矩阵搭建」与 第九章-4「自动化账号风控防封体系」。
    """

    __tablename__ = "reddit_account_profiles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    site_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("client_sites.id"), nullable=False, index=True
    )
    account_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("social_accounts.id"), nullable=False, unique=True, index=True
    )
    # warmup = 基础养号号 / seeding = 核心种草号 / expert = 专业答疑号
    role: Mapped[str] = mapped_column(String(20), default="warmup", nullable=False, index=True)
    # warmup_week1_2 / warmup_week3_4 / ready / active / warning / suspended
    stage: Mapped[str] = mapped_column(String(30), default="warmup_week1_2", nullable=False)
    karma: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    persona: Mapped[str | None] = mapped_column(String(200), nullable=True)  # 短展示名，兼容旧数据
    persona_config: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    daily_post_limit: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    daily_comment_limit: Mapped[int] = mapped_column(Integer, default=10, nullable=False)
    # 风控预警：低互动 / 限流时自动置为 warning，营销动作暂停
    risk_status: Mapped[str] = mapped_column(String(20), default="normal", nullable=False)  # normal / warning
    risk_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_warning_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    karma_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    account: Mapped["SocialAccount"] = relationship("SocialAccount")


class RedditCommunity(Base):
    """Reddit 社区（subreddit）库：垂直社区筛选与规则备忘。

    对应方案 第三章-2「精准社区筛选」。
    """

    __tablename__ = "reddit_communities"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    site_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("client_sites.id"), nullable=False, index=True
    )
    # 人设社区归账号独用；产品社区为 NULL，全站共用
    account_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("social_accounts.id"), nullable=True, index=True
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False, index=True)  # 不含 r/ 前缀
    # core = 核心垂直 / longtail = 长尾场景
    category: Mapped[str] = mapped_column(String(20), default="core", nullable=False)
    # persona = 人设兴趣社区 / promo = 用户手填的产品社区
    purpose: Mapped[str] = mapped_column(String(20), default="persona", nullable=False, index=True)
    rules_note: Mapped[str | None] = mapped_column(Text, nullable=True)  # 版规备忘（禁外链/固定日自推广等）
    rules_text: Mapped[str | None] = mapped_column(Text, nullable=True)  # Reddit 官方版规拼接
    rules_fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    allows_links: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    # 允许自推广的星期（0=周一 … 6=周日），NULL 表示不限
    promo_weekday: Mapped[int | None] = mapped_column(Integer, nullable=True)
    daily_post_limit: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    best_hour_utc: Mapped[int | None] = mapped_column(Integer, nullable=True)  # 海外活跃时段（UTC）
    priority: Mapped[int] = mapped_column(Integer, default=3, nullable=False)  # 1 最高
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    exists: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    subscribers: Mapped[int | None] = mapped_column(Integer, nullable=True)
    accounts_active: Mapped[int | None] = mapped_column(Integer, nullable=True)
    posts_7d: Mapped[int | None] = mapped_column(Integer, nullable=True)
    activity_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    verify_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    products: Mapped[list["RedditProduct"]] = relationship(
        "RedditProduct",
        secondary=reddit_product_communities,
        back_populates="communities",
    )


class RedditBrand(Base):
    """站点品牌：产品向评论生成时选择。"""

    __tablename__ = "reddit_brands"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    site_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("client_sites.id"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    products: Mapped[list["RedditProduct"]] = relationship(
        "RedditProduct", back_populates="brand", cascade="all, delete-orphan"
    )


class RedditProduct(Base):
    """品牌下的产品：名称、品类、可说卖点。"""

    __tablename__ = "reddit_products"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    brand_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("reddit_brands.id"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    category: Mapped[str] = mapped_column(String(120), default="", nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    talking_points: Mapped[list | None] = mapped_column(JSON, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    brand: Mapped["RedditBrand"] = relationship("RedditBrand", back_populates="products")
    communities: Mapped[list["RedditCommunity"]] = relationship(
        "RedditCommunity",
        secondary=reddit_product_communities,
        back_populates="products",
    )
    keyword_rows: Mapped[list["RedditProductKeyword"]] = relationship(
        "RedditProductKeyword",
        back_populates="product",
        cascade="all, delete-orphan",
        order_by="RedditProductKeyword.id",
    )


class RedditProductKeyword(Base):
    """产品绑定的社区搜索关键词。"""

    __tablename__ = "reddit_product_keywords"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    product_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("reddit_products.id", ondelete="CASCADE"), nullable=False, index=True
    )
    keyword: Mapped[str] = mapped_column(String(200), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, nullable=False
    )

    product: Mapped["RedditProduct"] = relationship("RedditProduct", back_populates="keyword_rows")


class RedditPostMetric(Base):
    """Reddit 帖子发布后数据快照（曝光/互动/收录跟踪）。

    对应方案 第七章「量化考核指标」与 第九章-3「数据自动汇总」。
    """

    __tablename__ = "reddit_post_metrics"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    post_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("reddit_posts.id"), nullable=False, index=True
    )
    score: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    num_comments: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    upvote_ratio: Mapped[float | None] = mapped_column(Float, nullable=True)
    synced_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, nullable=False
    )

    post: Mapped["RedditPost"] = relationship("RedditPost", back_populates="metrics")


from app.models.social import SocialAccount  # noqa: E402
