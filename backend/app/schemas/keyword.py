from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field, ConfigDict


class KeywordBase(BaseModel):
    keyword: str = Field(min_length=1, max_length=200)
    target_url: Optional[str] = None
    search_engine: str = "google"
    region: str = "us"
    priority: int = Field(default=3, ge=1, le=5)


class KeywordCreate(KeywordBase):
    pass


class KeywordOut(KeywordBase):
    id: int
    user_id: int
    site_id: int | None = None
    status: str
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)
