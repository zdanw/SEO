"""文章 CRUD + 状态机 + CMS 同步 API。"""
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.deps import SiteContext, get_current_user, get_site_context, require_site_write
from app.core.database import get_db
from app.models.user import User
from app.models.article import Article
from app.schemas.article import (
    ArticleCreate,
    ArticleOut,
    ArticleStatus,
    ArticleStatusUpdate,
    ArticleUpdate,
)
from app.services.cms_client import CmsSyncError, sync_article_to_cms
from app.services.page_fetcher import fetch_page_content

router = APIRouter()

_VALID_TRANSITIONS: dict[str, set[str]] = {
    "draft": {"ai_generated", "reviewed"},
    "ai_generated": {"reviewed", "draft"},
    "reviewed": {"published", "draft"},
    "published": {"reviewed"},
}


def _get_article(db: Session, article_id: int, site_id: int) -> Article | None:
    return (
        db.query(Article)
        .filter(Article.id == article_id, Article.site_id == site_id)
        .first()
    )


@router.get("", response_model=list[ArticleOut])
def list_articles(
    status_filter: ArticleStatus | None = Query(default=None, alias="status"),
    keyword_id: int | None = None,
    source: str | None = None,
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
    ctx: SiteContext = Depends(get_site_context),
) -> list[Article]:
    q = db.query(Article).filter(Article.site_id == ctx.site.id)
    if status_filter:
        q = q.filter(Article.status == status_filter)
    if keyword_id:
        q = q.filter(Article.keyword_id == keyword_id)
    if source:
        q = q.filter(Article.source == source)
    return (
        q.order_by(Article.updated_at.desc())
        .offset((page - 1) * size)
        .limit(size)
        .all()
    )


@router.post("", response_model=ArticleOut, status_code=status.HTTP_201_CREATED)
def create_article(
    payload: ArticleCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    ctx: SiteContext = Depends(require_site_write),
) -> Article:
    article = Article(
        **payload.model_dump(),
        user_id=current_user.id,
        site_id=ctx.site.id,
        status="draft",
    )
    db.add(article)
    db.commit()
    db.refresh(article)
    return article


@router.post("/import-url", response_model=ArticleOut, status_code=status.HTTP_201_CREATED)
def import_external_page(
    url: str = Query(..., min_length=1),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    ctx: SiteContext = Depends(require_site_write),
) -> Article:
    """从外部 URL 抓取页面并创建文章记录。"""
    try:
        page = fetch_page_content(url)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"页面抓取失败: {exc}") from exc
    article = Article(
        user_id=current_user.id,
        site_id=ctx.site.id,
        title=page["title"] or url,
        meta_description=page["meta_description"] or None,
        content=page["content"],
        cover_image_url=page.get("cover_image_url") or None,
        source="external",
        source_url=page.get("source_url") or url,
        target_url=page.get("source_url") or url,
        status="published",
        published_at=datetime.utcnow(),
    )
    db.add(article)
    db.commit()
    db.refresh(article)
    return article


@router.get("/{article_id}", response_model=ArticleOut)
def get_article(
    article_id: int,
    db: Session = Depends(get_db),
    ctx: SiteContext = Depends(get_site_context),
) -> Article:
    article = _get_article(db, article_id, ctx.site.id)
    if not article:
        raise HTTPException(status_code=404, detail="文章不存在")
    return article


@router.patch("/{article_id}", response_model=ArticleOut)
def update_article(
    article_id: int,
    payload: ArticleUpdate,
    db: Session = Depends(get_db),
    ctx: SiteContext = Depends(require_site_write),
) -> Article:
    article = _get_article(db, article_id, ctx.site.id)
    if not article:
        raise HTTPException(status_code=404, detail="文章不存在")
    update_data = payload.model_dump(exclude_unset=True)
    for k, v in update_data.items():
        setattr(article, k, v)
    db.commit()
    db.refresh(article)
    return article


@router.post("/{article_id}/status", response_model=ArticleOut)
def change_status(
    article_id: int,
    payload: ArticleStatusUpdate,
    db: Session = Depends(get_db),
    ctx: SiteContext = Depends(require_site_write),
) -> Article:
    article = _get_article(db, article_id, ctx.site.id)
    if not article:
        raise HTTPException(status_code=404, detail="文章不存在")
    allowed = _VALID_TRANSITIONS.get(article.status, set())
    if payload.status not in allowed and payload.status != article.status:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"非法状态流转：{article.status} → {payload.status}。"
            f"允许的目标：{sorted(allowed) or '无'}",
        )
    article.status = payload.status
    if payload.status == "published" and not article.published_at:
        article.published_at = datetime.utcnow()
    db.commit()
    db.refresh(article)
    return article


@router.post("/{article_id}/sync-cms", response_model=ArticleOut)
def sync_to_cms(
    article_id: int,
    db: Session = Depends(get_db),
    ctx: SiteContext = Depends(require_site_write),
) -> Article:
    """将文章同步到客户 CMS（WordPress 等）。"""
    article = _get_article(db, article_id, ctx.site.id)
    if not article:
        raise HTTPException(status_code=404, detail="文章不存在")
    try:
        result = sync_article_to_cms(ctx.site, article)
    except CmsSyncError as exc:
        article.sync_status = "failed"
        db.commit()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    article.cms_post_id = result.get("cms_post_id")
    article.source_url = result.get("source_url") or article.source_url
    article.target_url = result.get("source_url") or article.target_url
    article.sync_status = result.get("sync_status", "synced")
    article.cms_type = ctx.site.cms_type
    db.commit()
    db.refresh(article)
    return article


@router.delete("/{article_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_article(
    article_id: int,
    db: Session = Depends(get_db),
    ctx: SiteContext = Depends(require_site_write),
) -> None:
    article = _get_article(db, article_id, ctx.site.id)
    if not article:
        raise HTTPException(status_code=404, detail="文章不存在")
    db.delete(article)
    db.commit()
