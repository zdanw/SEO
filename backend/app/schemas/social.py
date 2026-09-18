from datetime import datetime
from typing import Optional, Literal

from pydantic import BaseModel, Field, ConfigDict


Platform = Literal["pulseforge", "linkedin", "twitter", "facebook", "reddit"]
PostStatus = Literal["pending", "scheduled", "posting", "posted", "failed", "cancelled"]


class SocialAccountBase(BaseModel):
    platform: Platform
    account_name: str = Field(min_length=1, max_length=100)
    access_token: Optional[str] = None
    config: Optional[dict] = None
    is_active: bool = True


class SocialAccountCreate(SocialAccountBase):
    pass


class SocialAccountUpdate(BaseModel):
    account_name: Optional[str] = None
    access_token: Optional[str] = None
    config: Optional[dict] = None
    is_active: Optional[bool] = None


class SocialAccountOut(SocialAccountBase):
    id: int
    user_id: int
    token_expires_at: Optional[datetime] = None
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


class SocialPostBase(BaseModel):
    account_id: int
    title: Optional[str] = Field(default=None, max_length=300)
    summary: Optional[str] = None
    image_url: Optional[str] = None
    external_url: Optional[str] = None
    hashtags: Optional[list[str]] = None
    scheduled_at: Optional[datetime] = None


class SocialPostCreate(SocialPostBase):
    pass


class SocialPostUpdate(BaseModel):
    title: Optional[str] = None
    summary: Optional[str] = None
    image_url: Optional[str] = None
    external_url: Optional[str] = None
    hashtags: Optional[list[str]] = None
    scheduled_at: Optional[datetime] = None


class SocialPostOut(SocialPostBase):
    id: int
    status: PostStatus
    platform_post_id: Optional[str] = None
    posted_at: Optional[datetime] = None
    engagement: Optional[dict] = None
    error_message: Optional[str] = None
    retry_count: int = 0
    created_at: datetime
    updated_at: datetime
    platform: Optional[str] = None
    account_name: Optional[str] = None
    model_config = ConfigDict(from_attributes=True)


class SocialPostSendNow(BaseModel):
    post_id: int
