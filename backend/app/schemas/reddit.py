from datetime import date, datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field, ConfigDict

PostType = Literal["auto", "pitfall", "vent", "unpopular", "guide", "help_seek"]
POST_TYPES: tuple[str, ...] = ("auto", "pitfall", "vent", "unpopular", "guide", "help_seek")
MANUAL_POST_TYPES: tuple[str, ...] = ("pitfall", "vent", "unpopular", "guide", "help_seek")
ReviewStatus = Literal[
    "draft", "pending_review", "approved", "rejected", "posting", "posted", "failed"
]
DiscoverSource = Literal["manual_url", "keyword_search", "auto_discover", "campaign"]
AccountRole = Literal["warmup", "seeding", "expert"]
AccountStage = Literal[
    "warmup_week1_2", "warmup_week3_4", "ready", "active", "warning", "suspended"
]
CommunityCategory = Literal["core", "longtail"]
CommunityPurpose = Literal["persona", "promo"]
ContentIntent = Literal["casual", "promo"]


class PersonaConfigIn(BaseModel):
    voice: str = ""
    interests: list[str] = []
    quirks: str = ""
    never_say: str = ""
    background: str = ""
    life_facts: list[str] = []
    signature_opinions: list[str] = []
    recurring_themes: list[str] = []
    # {subreddit: {voice/quirks/background/themes/life_facts}}
    subreddit_overrides: dict[str, dict] = {}


class RedditAccountOut(BaseModel):
    id: int
    account_name: str
    is_active: bool
    zernio_key_id: Optional[int] = None
    zernio_key_label: Optional[str] = None
    role: Optional[AccountRole] = None
    stage: Optional[AccountStage] = None
    karma: int = 0
    persona: Optional[str] = None
    persona_config: Optional[dict] = None
    daily_post_limit: int = 0
    daily_comment_limit: int = 0
    risk_status: str = "normal"
    risk_reason: Optional[str] = None
    posts_today: int = 0
    comments_today: int = 0
    model_config = ConfigDict(from_attributes=True)


class RedditStatusOut(BaseModel):
    configured: bool
    connected: bool
    accounts: list[RedditAccountOut] = []
    zernio_key_count: int = 0
    sync_errors: list[str] = []


class RedditAccountProfileUpdate(BaseModel):
    role: Optional[AccountRole] = None
    stage: Optional[AccountStage] = None
    karma: Optional[int] = Field(default=None, ge=0)
    persona: Optional[str] = Field(default=None, max_length=200)
    persona_config: Optional[PersonaConfigIn] = None
    daily_post_limit: Optional[int] = Field(default=None, ge=0, le=20)
    daily_comment_limit: Optional[int] = Field(default=None, ge=0, le=100)
    apply_role_defaults: bool = False
    clear_warning: bool = False


class PersonaGenerateIn(BaseModel):
    requirements: str = Field(min_length=1, max_length=2000)
    role: Optional[AccountRole] = None


class PersonaGenerateOut(BaseModel):
    voice: str = ""
    interests: list[str] = []
    quirks: str = ""
    never_say: str = ""
    background: str = ""
    life_facts: list[str] = []
    signature_opinions: list[str] = []
    recurring_themes: list[str] = []
    subreddit_overrides: dict[str, dict] = {}
    suggested_daily_post_limit: int = Field(default=0, ge=0, le=20)
    suggested_daily_comment_limit: int = Field(default=10, ge=0, le=100)


class RedditCommunityBase(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    category: CommunityCategory = "core"
    purpose: CommunityPurpose = "persona"
    rules_note: Optional[str] = None
    allows_links: bool = True
    promo_weekday: Optional[int] = Field(default=None, ge=0, le=6)
    daily_post_limit: int = Field(default=1, ge=0, le=10)
    best_hour_utc: Optional[int] = Field(default=None, ge=0, le=23)
    priority: int = Field(default=3, ge=1, le=5)
    is_active: bool = True


class RedditCommunityCreate(RedditCommunityBase):
    account_id: Optional[int] = None


class RedditCommunityUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=100)
    category: Optional[CommunityCategory] = None
    purpose: Optional[CommunityPurpose] = None
    rules_note: Optional[str] = None
    allows_links: Optional[bool] = None
    promo_weekday: Optional[int] = Field(default=None, ge=0, le=6)
    daily_post_limit: Optional[int] = Field(default=None, ge=0, le=10)
    best_hour_utc: Optional[int] = Field(default=None, ge=0, le=23)
    priority: Optional[int] = Field(default=None, ge=1, le=5)
    is_active: Optional[bool] = None


class RedditCommunityOut(RedditCommunityBase):
    id: int
    account_id: Optional[int] = None
    verified_at: Optional[datetime] = None
    exists: Optional[bool] = None
    subscribers: Optional[int] = None
    accounts_active: Optional[int] = None
    posts_7d: Optional[int] = None
    activity_score: Optional[float] = None
    verify_error: Optional[str] = None
    rules_text: Optional[str] = None
    rules_fetched_at: Optional[datetime] = None
    discovered_from: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    model_config = ConfigDict(from_attributes=True)


class ZernioKeyOut(BaseModel):
    id: int
    label: str
    api_key_masked: str
    profile_id: Optional[str] = None
    is_enabled: bool
    created_at: datetime


class ZernioKeyCreate(BaseModel):
    label: str = Field(min_length=1, max_length=100)
    api_key: str = Field(min_length=8, max_length=500)
    profile_id: Optional[str] = Field(default=None, max_length=100)


class ZernioKeyUpdate(BaseModel):
    label: Optional[str] = Field(default=None, min_length=1, max_length=100)
    api_key: Optional[str] = Field(default=None, min_length=8, max_length=500)
    profile_id: Optional[str] = Field(default=None, max_length=100)
    is_enabled: Optional[bool] = None


class RedditOAuthStartOut(BaseModel):
    auth_url: str


class RedditPostGenerateIn(BaseModel):
    account_id: int
    post_type: PostType = "auto"
    subreddit: str = Field(min_length=1, max_length=100)
    brand_id: Optional[int] = None
    product_id: Optional[int] = None
    allow_product: bool = False
    include_site_url: bool = False


class RedditPostUpdate(BaseModel):
    title: Optional[str] = Field(default=None, max_length=300)
    body: Optional[str] = None
    subreddit: Optional[str] = Field(default=None, max_length=100)


class RedditScheduleIn(BaseModel):
    scheduled_at: datetime


# ============ 自动化 Campaign ============
CampaignStatus = Literal["draft", "running", "paused", "ended"]
CampaignMode = Literal["auto", "review"]
CampaignActionType = Literal["post", "comment", "upvote_related", "upvote_self"]
CampaignActionStatus = Literal["pending", "executing", "done", "failed", "skipped"]


class RedditCampaignCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    start_date: date
    end_date: date
    active_hour_start: int = Field(default=0, ge=0, le=23)
    active_hour_end: int = Field(default=23, ge=0, le=23)
    content_mode: CampaignMode = "review"
    daily_posts: int = Field(default=0, ge=0, le=20)
    daily_comments: int = Field(default=0, ge=0, le=50)
    daily_upvotes: int = Field(default=0, ge=0, le=100)
    self_upvote_enabled: bool = False
    self_upvote_delay_minutes: int = Field(default=240, ge=10, le=1440)
    subreddits: list[str] = Field(default_factory=list, max_length=20)
    account_ids: list[int] = Field(default_factory=list, max_length=50)
    brand_id: Optional[int] = None
    product_id: Optional[int] = None


class RedditCampaignUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=120)
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    active_hour_start: Optional[int] = Field(default=None, ge=0, le=23)
    active_hour_end: Optional[int] = Field(default=None, ge=0, le=23)
    content_mode: Optional[CampaignMode] = None
    daily_posts: Optional[int] = Field(default=None, ge=0, le=20)
    daily_comments: Optional[int] = Field(default=None, ge=0, le=50)
    daily_upvotes: Optional[int] = Field(default=None, ge=0, le=100)
    self_upvote_enabled: Optional[bool] = None
    self_upvote_delay_minutes: Optional[int] = Field(default=None, ge=10, le=1440)
    subreddits: Optional[list[str]] = Field(default=None, max_length=20)
    account_ids: Optional[list[int]] = Field(default=None, max_length=50)
    brand_id: Optional[int] = None
    product_id: Optional[int] = None


class RedditCampaignOut(BaseModel):
    id: int
    site_id: int
    name: str
    status: CampaignStatus
    start_date: date
    end_date: date
    active_hour_start: int
    active_hour_end: int
    content_mode: CampaignMode
    daily_posts: int
    daily_comments: int
    daily_upvotes: int
    self_upvote_enabled: bool
    self_upvote_delay_minutes: int
    subreddits: list[str] = Field(default_factory=list)
    account_ids: list[int] = Field(default_factory=list)
    brand_id: Optional[int] = None
    product_id: Optional[int] = None
    # 今日执行进度（端点计算填充）
    today_posts: int = 0
    today_comments: int = 0
    today_upvotes: int = 0
    last_tick_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime
    model_config = ConfigDict(from_attributes=True)


class RedditCampaignActionOut(BaseModel):
    id: int
    campaign_id: int
    site_id: int
    account_id: int
    action_type: CampaignActionType
    status: CampaignActionStatus
    planned_at: Optional[datetime] = None
    executed_at: Optional[datetime] = None
    post_id: Optional[int] = None
    comment_id: Optional[int] = None
    target_thing_id: Optional[str] = None
    target_post_url: Optional[str] = None
    error_message: Optional[str] = None
    skip_reason: Optional[str] = None
    account_name: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    model_config = ConfigDict(from_attributes=True)


class RedditBatchRetryIn(BaseModel):
    action_ids: list[int] = Field(default_factory=list, max_length=100)


class RedditBatchRetryItem(BaseModel):
    action_id: int
    ok: bool
    detail: Optional[str] = None


class RedditBatchRetryOut(BaseModel):
    retried: int
    skipped: int
    items: list[RedditBatchRetryItem] = Field(default_factory=list)


class RedditCampaignReportOut(BaseModel):
    """Campaign 按日执行报告：配额达成与失败/跳过明细。"""

    campaign_id: int
    date: date
    planned: int = 0
    by_status: dict[str, int] = Field(default_factory=dict)
    by_type: dict[str, int] = Field(default_factory=dict)
    skip_reasons: dict[str, int] = Field(default_factory=dict)
    failed_action_ids: list[int] = Field(default_factory=list)


class RedditPostOut(BaseModel):
    id: int
    site_id: int
    account_id: int
    post_type: PostType
    subreddit: str
    keyword: str
    title: str
    body: str
    site_url: Optional[str] = None
    status: ReviewStatus
    reddit_post_id: Optional[str] = None
    reddit_permalink: Optional[str] = None
    campaign_id: Optional[int] = None
    campaign_name: Optional[str] = None
    scheduled_at: Optional[datetime] = None
    planned_at: Optional[datetime] = None
    published_at: Optional[datetime] = None
    error_message: Optional[str] = None
    content_intent: ContentIntent = "promo"
    ai_risk: Optional[str] = None
    ai_score: Optional[float] = None
    account_name: Optional[str] = None
    queue_no: Optional[int] = None  # 审核队列序号：账号内按创建顺序编号
    created_at: datetime
    updated_at: datetime
    model_config = ConfigDict(from_attributes=True)


class RedditCommentGenerateIn(BaseModel):
    account_id: int
    target_post_url: str = Field(min_length=10, max_length=500)
    include_site_url: bool = False
    content_intent: Optional[ContentIntent] = None
    brand_id: Optional[int] = None
    product_id: Optional[int] = None
    target_post_title: Optional[str] = Field(default=None, max_length=500)
    target_post_body: Optional[str] = Field(default=None, max_length=5000)


class RedditCommentBatchPostIn(BaseModel):
    url: str = Field(min_length=10, max_length=500)
    title: Optional[str] = Field(default=None, max_length=500)
    body: Optional[str] = Field(default=None, max_length=5000)


class RedditCommentBatchGenerateIn(BaseModel):
    account_id: int
    subreddit: str = Field(min_length=1, max_length=100)
    keyword: str = Field(min_length=1, max_length=200)
    posts: list[RedditCommentBatchPostIn] = Field(min_length=1, max_length=25)
    include_site_url: bool = False
    content_intent: Optional[ContentIntent] = None
    brand_id: Optional[int] = None
    product_id: Optional[int] = None


class RedditCommentUpdate(BaseModel):
    body: Optional[str] = None


class RedditCommentOut(BaseModel):
    id: int
    site_id: int
    account_id: int
    target_post_url: str
    target_thing_id: str
    subreddit: str
    target_post_title: Optional[str] = None
    body: str
    keyword: Optional[str] = None
    discover_source: DiscoverSource
    content_intent: ContentIntent = "casual"
    ai_risk: Optional[str] = None
    ai_score: Optional[float] = None
    brand_id: Optional[int] = None
    product_id: Optional[int] = None
    brand_name: Optional[str] = None
    product_name: Optional[str] = None
    status: ReviewStatus
    reddit_comment_id: Optional[str] = None
    campaign_id: Optional[int] = None
    campaign_name: Optional[str] = None
    scheduled_at: Optional[datetime] = None
    planned_at: Optional[datetime] = None
    published_at: Optional[datetime] = None
    error_message: Optional[str] = None
    account_name: Optional[str] = None
    queue_no: Optional[int] = None  # 审核队列序号：账号内按创建顺序编号
    created_at: datetime
    updated_at: datetime
    model_config = ConfigDict(from_attributes=True)


class RedditDiscoverItem(BaseModel):
    title: str
    url: str
    thing_id: str
    subreddit: str
    score: int = 0
    num_comments: int = 0
    created_utc: int = 0
    body: str = ""


class RedditEngageCommentOut(BaseModel):
    thing_id: str
    body: str = ""
    author: str = ""
    score: int = 0
    created_utc: int = 0
    url: str = ""


class RedditEngageCommentsOut(BaseModel):
    items: list[RedditEngageCommentOut]


class RedditEngageFeedOut(BaseModel):
    items: list[RedditDiscoverItem]


class RedditVoteIn(BaseModel):
    account_id: int
    thing_id: str = Field(min_length=3, max_length=64)
    direction: int = Field(default=1, ge=-1, le=1)


class RedditVoteOut(BaseModel):
    ok: bool = True
    thing_id: str
    direction: int


class RedditDiscoverMeta(BaseModel):
    auto_mode: bool = False
    queued_count: int = 0
    skipped_count: int = 0
    errors: int = 0


class RedditSmartDiscoverIn(BaseModel):
    account_id: int
    subreddits: list[str] = Field(min_length=1, max_length=20)
    brand_id: Optional[int] = None
    product_id: Optional[int] = None


class RedditCommunitySuggestIn(BaseModel):
    account_id: Optional[int] = None
    interests: list[str] = Field(default_factory=list, max_length=12)


class RedditCommunitySuggestOut(BaseModel):
    suggested: list[str]
    added: int = 0


class RedditProductIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    category: str = Field(default="", max_length=120)
    description: str = Field(default="", max_length=2000)
    talking_points: list[str] = Field(default_factory=list, max_length=8)
    keywords: list[str] = Field(default_factory=list, max_length=20)
    is_active: bool = True
    community_ids: list[int] = Field(default_factory=list)


class RedditProductUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=120)
    category: Optional[str] = Field(default=None, max_length=120)
    description: Optional[str] = Field(default=None, max_length=2000)
    talking_points: Optional[list[str]] = Field(default=None, max_length=8)
    keywords: Optional[list[str]] = Field(default=None, max_length=20)
    is_active: Optional[bool] = None
    community_ids: Optional[list[int]] = None


class RedditProductOut(BaseModel):
    id: int
    brand_id: int
    brand_name: str = ""
    name: str
    category: str = ""
    description: str = ""
    talking_points: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    is_active: bool = True
    community_ids: list[int] = Field(default_factory=list)
    community_names: list[str] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime
    model_config = ConfigDict(from_attributes=True)


class RedditBrandIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    is_active: bool = True


class RedditBrandUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=120)
    is_active: Optional[bool] = None


class RedditBrandOut(BaseModel):
    id: int
    site_id: int
    name: str
    is_active: bool = True
    products: list[RedditProductOut] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime
    model_config = ConfigDict(from_attributes=True)


class RedditDiscoverResponse(BaseModel):
    items: list[RedditDiscoverItem]
    meta: RedditDiscoverMeta


class RedditMetricOut(BaseModel):
    id: int
    post_id: int
    score: int
    num_comments: int
    upvote_ratio: Optional[float] = None
    synced_at: datetime
    model_config = ConfigDict(from_attributes=True)


class RedditRiskOut(BaseModel):
    ok: bool
    errors: list[str] = []
    warnings: list[str] = []


class RedditOverviewOut(BaseModel):
    posts_pending: int = 0
    posts_approved: int = 0
    posts_posted: int = 0
    posts_posted_today: int = 0
    posts_scheduled: int = 0
    comments_pending: int = 0
    comments_posted_today: int = 0
    accounts_warning: int = 0
    recent_avg_score: Optional[float] = None
    recent_total_score: int = 0
    recent_total_comments: int = 0
    promo_ratio_7d: float = 0.0
    promo_count_7d: int = 0
    casual_count_7d: int = 0
    persona_communities: int = 0
    promo_communities: int = 0
    comments_likely_ai: int = 0
