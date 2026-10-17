from datetime import date, datetime
from app.core.timeutil import now_bj

import sqlalchemy as sa
from sqlalchemy import (
    Boolean,
    Column,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Table,
    Text,
    UniqueConstraint,
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
    # 本次生成使用的叙事角度（personal_incident/hard_lesson/...），表现加权用
    narrative_angle: Mapped[str | None] = mapped_column(String(40), nullable=True)
    ai_risk: Mapped[str | None] = mapped_column(String(20), nullable=True)
    ai_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    campaign_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("reddit_campaigns.id", ondelete="SET NULL"), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=now_bj, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=now_bj, onupdate=now_bj, nullable=False
    )

    __table_args__ = (Index("ix_reddit_posts_status_scheduled", "status", "scheduled_at"),)

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
    ai_score: Mapped[float | None] = mapped_column(Float, nullable=True)
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
    campaign_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("reddit_campaigns.id", ondelete="SET NULL"), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=now_bj, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=now_bj, onupdate=now_bj, nullable=False
    )

    __table_args__ = (Index("ix_reddit_comments_status_scheduled", "status", "scheduled_at"),)

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
        DateTime(timezone=True), default=now_bj, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=now_bj, onupdate=now_bj, nullable=False
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
    # 扩池溯源：由哪个优质社区扩散发现（NULL = 手动/兴趣建议）
    discovered_from: Mapped[str | None] = mapped_column(String(100), nullable=True)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    exists: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    subscribers: Mapped[int | None] = mapped_column(Integer, nullable=True)
    accounts_active: Mapped[int | None] = mapped_column(Integer, nullable=True)
    posts_7d: Mapped[int | None] = mapped_column(Integer, nullable=True)
    activity_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    verify_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=now_bj, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=now_bj, onupdate=now_bj, nullable=False
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
        DateTime(timezone=True), default=now_bj, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=now_bj, onupdate=now_bj, nullable=False
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
        DateTime(timezone=True), default=now_bj, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=now_bj, onupdate=now_bj, nullable=False
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
        DateTime(timezone=True), default=now_bj, nullable=False
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
        DateTime(timezone=True), default=now_bj, nullable=False
    )

    post: Mapped["RedditPost"] = relationship("RedditPost", back_populates="metrics")


class RedditCampaign(Base):
    """Reddit 自动化 Campaign：在指定时间段内自动执行发帖/评论/点赞系列操作。

    调度引擎（campaign_tick）每 15 分钟扫描 running 状态的 campaign，
    按当日配额 + 活跃时段随机生成动作并复用现有定时发布链路。
    """

    __tablename__ = "reddit_campaigns"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    site_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("client_sites.id"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    # draft / running / paused / ended
    status: Mapped[str] = mapped_column(String(20), default="draft", nullable=False, index=True)
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date] = mapped_column(Date, nullable=False)
    # 每日活跃时段（UTC 小时，0-23），执行时间随机裁剪到该窗口内
    active_hour_start: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    active_hour_end: Mapped[int] = mapped_column(Integer, default=23, nullable=False)
    # auto = AI 生成后直接定时发布 / review = 进入待审队列人工批准
    content_mode: Mapped[str] = mapped_column(String(20), default="review", nullable=False)
    daily_posts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    daily_comments: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    daily_upvotes: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    self_upvote_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    self_upvote_delay_minutes: Mapped[int] = mapped_column(Integer, default=240, nullable=False)
    # 发帖目标社区（不含 r/ 前缀）；评论/点赞目标自动发现
    subreddits: Mapped[list | None] = mapped_column(JSON, nullable=True)
    # 账号池（social_accounts.id 列表）
    account_ids: Mapped[list | None] = mapped_column(JSON, nullable=True)
    brand_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("reddit_brands.id"), nullable=True
    )
    product_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("reddit_products.id"), nullable=True
    )
    # 配置版本：PATCH 更新配置时递增；动作规划时快照到动作行，执行前校验
    config_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    last_tick_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=now_bj, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=now_bj, onupdate=now_bj, nullable=False
    )


class RedditCampaignAction(Base):
    """Campaign 动作执行记录：计划 → 执行 → 结果，含防重 dedup_key。

    状态机（阶段 1 扩展）：pending / executing / done / failed / skipped /
    reconciling（外部结果待核实）/ needs_review（自动恢复不安全，转人工）。
    生成与执行是双轴生命周期：status 管执行，generation_status 管生成
    （阶段 2 启用，阶段 1 默认 not_required）。
    """

    __tablename__ = "reddit_campaign_actions"
    __table_args__ = (
        UniqueConstraint("campaign_id", "dedup_key", name="uq_campaign_action_dedup"),
        # 排期引擎高频查询：账号维度取 pending/executing 忙碌区间 + campaign 配额统计
        Index(
            "ix_campaign_actions_account_status_planned",
            "account_id",
            "status",
            "planned_at",
        ),
        Index("ix_campaign_actions_campaign_planned", "campaign_id", "planned_at"),
        # 僵尸回收 / 对账扫描：仅执行中的行（部分索引）
        Index(
            "ix_campaign_actions_status_lease_until",
            "status",
            "lease_until",
            postgresql_where=sa.text("status IN ('executing', 'reconciling')"),
        ),
        # 执行发放扫描（due_upvote_action_ids / claim_due_actions_batch）：
        # 仅 pending 点赞（r023 部分索引；负载测试实测 42k 行上全 planned_at
        # 索引需过滤 30k 行，部分索引精确命中 due 集合）
        Index(
            "ix_campaign_actions_due_dispatch",
            "planned_at",
            postgresql_where=sa.text(
                "status = 'pending' AND action_type IN ('upvote_related', 'upvote_self')"
            ),
        ),
        # 生成心跳重投扫描（requeue_stale_generation_actions）：仅 pending 内容骨架
        # 的 queued/generating 行（r023 部分索引）
        Index(
            "ix_campaign_actions_gen_requeue",
            "planned_at",
            postgresql_where=sa.text(
                "status = 'pending' AND action_type IN ('post', 'comment') "
                "AND generation_status IN ('queued', 'generating')"
            ),
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    campaign_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("reddit_campaigns.id", ondelete="CASCADE"), nullable=False, index=True
    )
    site_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("client_sites.id"), nullable=False, index=True
    )
    account_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("social_accounts.id"), nullable=False, index=True
    )
    # post / comment / upvote_related / upvote_self
    action_type: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    # pending / executing / done / failed / skipped / reconciling / needs_review
    status: Mapped[str] = mapped_column(String(20), default="pending", nullable=False, index=True)
    planned_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    executed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    post_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("reddit_posts.id", ondelete="SET NULL"), nullable=True, index=True
    )
    comment_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("reddit_comments.id", ondelete="SET NULL"), nullable=True, index=True
    )
    target_thing_id: Mapped[str | None] = mapped_column(String(50), nullable=True)
    target_post_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    skip_reason: Mapped[str | None] = mapped_column(String(200), nullable=True)
    # 防重复计划：post:{campaign}:{account}:{yyyymmdd} / upvote:{account}:{thing} / upvote_self:{voter}:{post}
    dedup_key: Mapped[str] = mapped_column(String(150), nullable=False)
    # ---- 阶段 1 可靠性：租约 / fencing 代次 / 协议列 ----
    # 执行租约持有者（观测用；终态回写校验以代次为准，worker 身份可能被复用）
    lease_owner: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # 两阶段独立 fencing 代次：各自领取 +1，续租/终态回写必须校验所属阶段代次
    execution_lease_generation: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    generation_lease_generation: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # 执行租约到期时刻（僵尸回收按此判定，替代 updated_at 时间推断）
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # 投递可用时刻（重试退避 / 到期调度）
    available_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # 生成生命周期：not_required / queued / generating / ready / failed（阶段 2 启用）
    generation_status: Mapped[str] = mapped_column(
        String(20), default="not_required", nullable=False
    )
    # 按阶段独立尝试计数与重试预算（生成重试不占执行预算）
    generation_attempt_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    execution_attempt_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    generation_max_attempts: Mapped[int] = mapped_column(Integer, default=2, nullable=False)
    execution_max_attempts: Mapped[int] = mapped_column(Integer, default=2, nullable=False)
    # 外部结果分类：confirmed_success / confirmed_absent / unknown / config_version_mismatch
    result_status: Mapped[str | None] = mapped_column(String(30), nullable=True)
    # 错误分类码：retryable_rate_limit / request_unknown / paused_before_request / ...
    last_error_code: Mapped[str | None] = mapped_column(String(50), nullable=True)
    # 暂停/取消协议：暂停时对在途动作打取消请求时刻（执行前校验点据此停止）
    cancel_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # 规划时快照的 campaign 配置版本（执行前校验）
    config_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    # 生成上下文快照（JSON 字符串）：评论候选 {url, thing_id, title, body, subreddit,
    # keyword} 随骨架持久化，generation 队列任务免重复抓取；帖子上下文全可重推导，为空
    generation_context: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=now_bj, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=now_bj, onupdate=now_bj, nullable=False
    )


class RedditCampaignActionAttempt(Base):
    """动作尝试审计（两段持久化）：领取事务预写 request_pending，终态事务回写 responded。

    恢复程序依据：attempt_no 对应当前租约代次；request_started_at 非空表示外部请求
    已开始发出——worker 在请求发出后、响应回写前崩溃时据此进入 reconciling，
    而非凭时间推断盲目重试。
    """

    __tablename__ = "reddit_campaign_action_attempts"
    __table_args__ = (
        UniqueConstraint("action_id", "phase", "attempt_no", name="uq_campaign_action_attempt"),
        Index("ix_campaign_attempts_action_phase", "action_id", "phase"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    action_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("reddit_campaign_actions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # generation / execution
    phase: Mapped[str] = mapped_column(String(20), nullable=False)
    # 该动作该阶段内序号（执行阶段与 execution_lease_generation 同步递增）
    attempt_no: Mapped[int] = mapped_column(Integer, nullable=False)
    # request_pending（预写，请求尚未发出）→ responded（终态回写）
    request_state: Mapped[str] = mapped_column(String(20), nullable=False)
    worker_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    request_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    responded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(50), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 外部对象 ID（thing_id / 外部请求标识，供对账直查核实）
    external_object_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    result_summary: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=now_bj, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=now_bj, onupdate=now_bj, nullable=False
    )


class RedditVoteLog(Base):
    """Reddit 投票记录（点赞审计）：unique(account, thing) + upsert。

    兼作「该账号是否已赞过」查询依据，避免重复投票触发 Reddit 风控。
    """

    __tablename__ = "reddit_vote_logs"
    __table_args__ = (
        UniqueConstraint("account_id", "thing_id", name="uq_vote_log_account_thing"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    site_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("client_sites.id"), nullable=False, index=True
    )
    account_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("social_accounts.id"), nullable=False, index=True
    )
    thing_id: Mapped[str] = mapped_column(String(50), nullable=False)
    direction: Mapped[int] = mapped_column(Integer, default=1, nullable=False)  # 1 / 0 / -1
    # 投票时目标帖分数快照（best-effort）：验证投票效果 / 自赞后分数异常衰减检测
    score_at_vote: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # campaign / manual
    source: Mapped[str] = mapped_column(String(20), default="manual", nullable=False)
    campaign_action_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("reddit_campaign_actions.id", ondelete="SET NULL"), nullable=True
    )
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=now_bj, nullable=False
    )


from app.models.social import SocialAccount  # noqa: E402


class RedditCommunityActivity(Base):
    """账号×社区活动账本：加权轮转 / 冷却 / 内容角度去重 / 表现降权的数据基础。

    unique(account_id, subreddit) + upsert 避免重复行；recent_angles 保存最近使用过的
    叙事角度（最近优先），用于同社区内的角度轮换与重复回避。
    """

    __tablename__ = "reddit_community_activity"
    __table_args__ = (
        UniqueConstraint("account_id", "subreddit", name="uq_community_activity_account_sub"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    site_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("client_sites.id"), nullable=False, index=True
    )
    account_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("social_accounts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    subreddit: Mapped[str] = mapped_column(String(100), nullable=False)
    last_action_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    action_count_7d: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    action_count_30d: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # 表现反馈降权（越大越少用）；cooldown_until 前不参与轮转
    penalty: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    cooldown_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # 最近使用的叙事角度（最近优先，最多 8 个）
    recent_angles: Mapped[list | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=now_bj, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=now_bj, onupdate=now_bj, nullable=False
    )
