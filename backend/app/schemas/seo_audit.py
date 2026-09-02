from datetime import datetime
from decimal import Decimal
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field


class SeoAuditCreate(BaseModel):
    max_pages: int = Field(default=50, ge=1, le=200)


class SeoAuditPageOut(BaseModel):
    id: int
    audit_id: int
    url: str
    title: Optional[str] = None
    score: Optional[Decimal] = None
    issues_count: int
    detail_json: Optional[Any] = None
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


class SeoAuditOut(BaseModel):
    id: int
    site_id: int
    status: str
    total_pages: int
    scanned_pages: int
    avg_score: Optional[Decimal] = None
    summary: Optional[str] = None
    created_at: datetime
    completed_at: Optional[datetime] = None
    pages: list[SeoAuditPageOut] = []
    model_config = ConfigDict(from_attributes=True)


class UrlCheckRequest(BaseModel):
    url: str = Field(min_length=1, max_length=500)
    keyword: Optional[str] = None
