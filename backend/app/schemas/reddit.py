from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field, ConfigDict

PostType = Literal["pitfall", "vent", "unpopular", "guide", "help_seek"]
POST_TYPES: tuple[str, ...] = ("pitfall", "vent", "unpopular", "guide", "help_seek")
ReviewStatus = Literal[
    "draft", "pending_review", "approved", "rejected", "posting", "posted", "failed"
]
DiscoverSource = Literal["manual_url", "keyword_search", "auto_discover"]
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
    post_type: PostType
    subreddit: str = Field(min_length=1, max_length=100)
    brand_id: Optional[int] = None
    product_id: Optional[int] = None
    include_site_url: bool = False


class RedditPostUpdate(BaseModel):
    title: Optional[str] = Field(default=None, max_length=300)
    body: Optional[str] = None
    subreddit: Optional[str] = Field(default=None, max_length=100)


class RedditScheduleIn(BaseModel):
    scheduled_at: datetime


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
    scheduled_at: Optional[datetime] = None
    published_at: Optional[datetime] = None
    error_message: Optional[str] = None
    content_intent: ContentIntent = "promo"
    account_name: Optional[str] = None
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
    brand_id: Optional[int] = None
    product_id: Optional[int] = None
    brand_name: Optional[str] = None
    product_name: Optional[str] = None
    status: ReviewStatus
    reddit_comment_id: Optional[str] = None
    scheduled_at: Optional[datetime] = None
    published_at: Optional[datetime] = None
    error_message: Optional[str] = None
    account_name: Optional[str] = None
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
