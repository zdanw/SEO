from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


SiteStatus = Literal["active", "paused", "archived"]
SiteRole = Literal["admin", "operator", "client_viewer"]
CmsType = Literal["none", "wordpress", "shopify", "custom"]


class ClientSiteBase(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    domain: str = Field(min_length=1, max_length=200)
    industry: Optional[str] = Field(default=None, max_length=100)
    cms_type: CmsType = "none"
    cms_api_url: Optional[str] = Field(default=None, max_length=500)
    cms_api_key: Optional[str] = None
    sitemap_url: Optional[str] = Field(default=None, max_length=500)
    notes: Optional[str] = None


class ClientSiteCreate(ClientSiteBase):
    pass


class ClientSiteUpdate(BaseModel):
    name: Optional[str] = Field(default=None, max_length=200)
    domain: Optional[str] = Field(default=None, max_length=200)
    status: Optional[SiteStatus] = None
    industry: Optional[str] = Field(default=None, max_length=100)
    cms_type: Optional[CmsType] = None
    cms_api_url: Optional[str] = Field(default=None, max_length=500)
    cms_api_key: Optional[str] = None
    sitemap_url: Optional[str] = Field(default=None, max_length=500)
    notes: Optional[str] = None


class ClientSiteOut(ClientSiteBase):
    id: int
    owner_user_id: int
    status: SiteStatus
    created_at: datetime
    updated_at: datetime
    role: Optional[SiteRole] = None
    model_config = ConfigDict(from_attributes=True)


class SiteMemberCreate(BaseModel):
    user_id: int
    role: SiteRole = "operator"


class SiteMemberOut(BaseModel):
    id: int
    site_id: int
    user_id: int
    role: SiteRole
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)
