"""P3 监控模块 Pydantic 模型。"""
from datetime import datetime
from typing import Optional, Literal

from pydantic import BaseModel, Field, ConfigDict

CrawlStatus = Literal["success", "not_found", "blocked", "timeout", "error"]


# ============ SERP 排名快照 ============
class SerpRankSnapshotOut(BaseModel):
    time: datetime
    keyword_id: int
    target_url: Optional[str] = None
    rank: Optional[int] = None
    page: Optional[int] = None
    serp_features: Optional[dict] = None
    search_region: str
    proxy_used: Optional[str] = None
    crawl_status: CrawlStatus
    error_message: Optional[str] = None
    keyword: Optional[str] = None  # 冗余展示字段
    model_config = ConfigDict(from_attributes=True)


class LatestRankOut(BaseModel):
    keyword_id: int
    keyword: str
    target_url: Optional[str] = None
    rank: Optional[int] = None
    crawl_status: str
    last_crawled: Optional[str] = None




# ============ Backlink ============
class BacklinkBase(BaseModel):
    target_url: str = Field(min_length=1, max_length=500)
    source_url: str = Field(min_length=1, max_length=500)
    anchor_text: Optional[str] = Field(default=None, max_length=300)
    domain_authority: Optional[int] = Field(default=None, ge=0, le=100)


class BacklinkCreate(BacklinkBase):
    pass


class BacklinkUpdate(BaseModel):
    anchor_text: Optional[str] = None
    domain_authority: Optional[int] = None
    is_alive: Optional[bool] = None


class BacklinkOut(BacklinkBase):
    id: int
    user_id: int
    is_alive: bool
    first_seen_at: datetime
    last_checked_at: Optional[datetime] = None
    lost_at: Optional[datetime] = None
    model_config = ConfigDict(from_attributes=True)


# ============ Competitor ============
class CompetitorBase(BaseModel):
    domain: str = Field(min_length=1, max_length=200)
    name: Optional[str] = Field(default=None, max_length=100)


class CompetitorCreate(CompetitorBase):
    pass


class CompetitorOut(CompetitorBase):
    id: int
    user_id: int
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


class CompetitorRankOut(BaseModel):
    time: datetime
    competitor_id: int
    keyword_id: int
    domain: str
    rank: Optional[int] = None
    target_url: Optional[str] = None
    keyword: Optional[str] = None  # 冗余展示字段
    model_config = ConfigDict(from_attributes=True)
