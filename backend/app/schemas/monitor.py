"""P3 监控模块 Pydantic 模型。"""
from datetime import datetime
from typing import Optional, Literal

from pydantic import BaseModel, ConfigDict

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

