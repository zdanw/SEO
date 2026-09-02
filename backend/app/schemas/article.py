from datetime import datetime
from decimal import Decimal
from typing import Optional, Literal, Any

from pydantic import BaseModel, Field, ConfigDict


# ============ Keyword ============
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


# ============ Article ============
ArticleStatus = Literal["draft", "ai_generated", "reviewed", "published"]


class ArticleBase(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    meta_description: Optional[str] = Field(default=None, max_length=320)
    slug: Optional[str] = Field(default=None, max_length=300)
    content: Optional[str] = None
    cover_image_url: Optional[str] = None
    target_url: Optional[str] = None
    keyword_id: Optional[int] = None
    source: Literal["internal", "external"] = "internal"
    source_url: Optional[str] = None


class ArticleCreate(ArticleBase):
    pass


class ArticleUpdate(BaseModel):
    title: Optional[str] = Field(default=None, max_length=300)
    meta_description: Optional[str] = Field(default=None, max_length=320)
    slug: Optional[str] = Field(default=None, max_length=300)
    content: Optional[str] = None
    cover_image_url: Optional[str] = None
    target_url: Optional[str] = None
    keyword_id: Optional[int] = None


class ArticleStatusUpdate(BaseModel):
    """文章状态机切换。"""
    status: ArticleStatus


class ArticleOut(ArticleBase):
    id: int
    user_id: int
    site_id: int | None = None
    status: ArticleStatus
    cms_type: Optional[str] = None
    cms_post_id: Optional[str] = None
    sync_status: str = "none"
    seo_score: Optional[Decimal] = None
    ai_detected_score: Optional[Decimal] = None
    seo_detail: Optional[Any] = None
    published_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime
    model_config = ConfigDict(from_attributes=True)


# ============ AI 写作 ============
AIModelProvider = Literal["deepseek", "agnes"]


class AIArticleRequest(BaseModel):
    """AI 生成文章请求。"""
    keyword: str = Field(min_length=1, max_length=200)
    outline: Optional[str] = None
    audience: str = "技术从业者和内容创作者"
    tone: str = "专业、务实、易懂"
    model: AIModelProvider = "deepseek"


class AIArticleResponse(BaseModel):
    """AI 生成文章响应。"""
    title: str
    meta_description: str
    content: str
    keywords_suggested: list[str] = []


class AIMetaRequest(BaseModel):
    """AI 重新生成 Meta Description。"""
    keyword: str
    content: str


class AISocialCopyRequest(BaseModel):
    """AI 生成社交差异化文案。"""
    platform: Literal["LinkedIn", "Twitter/X", "Facebook", "PulseForge", "Reddit"]
    article_title: str
    keyword: str
    summary: str


class AISocialCopyResponse(BaseModel):
    platform: str
    title: str
    summary: str
    hashtags: list[str] = []


# ============ InternalLink ============
class InternalLinkOut(BaseModel):
    target_article_id: int
    target_title: str
    anchor_text: str
    relevance_score: Optional[Decimal] = None
    model_config = ConfigDict(from_attributes=True)
