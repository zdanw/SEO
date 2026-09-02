from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field, ConfigDict

PostType = Literal["consultation", "experience"]
ReviewStatus = Literal[
    "draft", "pending_review", "approved", "rejected", "posting", "posted", "failed"
]
DiscoverSource = Literal["manual_url", "keyword_search", "auto_discover"]


class RedditAccountOut(BaseModel):
    id: int
    account_name: str
    is_active: bool
    model_config = ConfigDict(from_attributes=True)


class RedditStatusOut(BaseModel):
    configured: bool
    connected: bool
    accounts: list[RedditAccountOut] = []


class RedditOAuthStartOut(BaseModel):
    auth_url: str


class RedditPostGenerateIn(BaseModel):
    account_id: int
    post_type: PostType
    subreddit: str = Field(min_length=1, max_length=100)
    keyword: str = Field(min_length=1, max_length=200)
    include_site_url: bool = False


class RedditPostUpdate(BaseModel):
    title: Optional[str] = Field(default=None, max_length=300)
    body: Optional[str] = None
    subreddit: Optional[str] = Field(default=None, max_length=100)


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
    error_message: Optional[str] = None
    account_name: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    model_config = ConfigDict(from_attributes=True)


class RedditCommentGenerateIn(BaseModel):
    account_id: int
    target_post_url: str = Field(min_length=10, max_length=500)
    include_site_url: bool = False


class RedditCommentBatchGenerateIn(BaseModel):
    account_id: int
    subreddit: str = Field(min_length=1, max_length=100)
    keyword: str = Field(min_length=1, max_length=200)
    post_urls: list[str] = Field(min_length=1, max_length=25)
    include_site_url: bool = False


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
    status: ReviewStatus
    reddit_comment_id: Optional[str] = None
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


class RedditDiscoverMeta(BaseModel):
    auto_mode: bool = False
    queued_count: int = 0


class RedditDiscoverResponse(BaseModel):
    items: list[RedditDiscoverItem]
    meta: RedditDiscoverMeta
