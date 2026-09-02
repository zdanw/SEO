from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class GscStatusOut(BaseModel):
    configured: bool
    connected: bool
    google_email: Optional[str] = None
    site_url: Optional[str] = None
    updated_at: Optional[datetime] = None


class GscOAuthStartOut(BaseModel):
    auth_url: str


class GscSiteOut(BaseModel):
    site_url: str
    permission_level: Optional[str] = None


class GscSiteSelectIn(BaseModel):
    site_url: str = Field(..., min_length=1, max_length=500)


class GscAnalyticsRowOut(BaseModel):
    key: str
    clicks: int
    impressions: int
    ctr: float
    position: float


class GscAnalyticsOut(BaseModel):
    start_date: str
    end_date: str
    site_url: str
    dimension: str
    rows: list[GscAnalyticsRowOut]


class GscSummaryOut(BaseModel):
    start_date: str
    end_date: str
    site_url: str
    clicks: int
    impressions: int
    ctr: float
    position: float
