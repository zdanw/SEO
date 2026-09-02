"""社交分发 API：账号配置 + 发帖任务 CRUD + 联动触发 + 立即发送。"""
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.orm import Session

from app.api.deps import SiteContext, get_current_user, get_site_context, require_site_write
from app.core.database import get_db
from app.models.user import User
from app.models.article import Article
from app.models.social import SocialAccount, SocialPost
from app.schemas.social import (
    SocialAccountCreate, SocialAccountUpdate, SocialAccountOut,
    SocialPostCreate, SocialPostUpdate, SocialPostOut,
    AutoDistributeRequest, AutoDistributeResponse,
    SocialPostSendNow,
)
from app.services.ai_writer import DeepSeekError, get_ai_client
from app.services.pulseforge_client import (
    get_platform_client, PublishPayload, PlatformError,
)
from app.tasks.social_tasks import send_post_task

router = APIRouter()


# ============ 账号配置 ============
@router.get("/accounts", response_model=list[SocialAccountOut], tags=["社交账号"])
def list_accounts(
    db: Session = Depends(get_db),
    ctx: SiteContext = Depends(get_site_context),
) -> list[SocialAccount]:
    return (
        db.query(SocialAccount)
        .filter(SocialAccount.site_id == ctx.site.id)
        .order_by(SocialAccount.created_at.desc())
        .all()
    )


@router.post("/accounts", response_model=SocialAccountOut, status_code=status.HTTP_201_CREATED, tags=["社交账号"])
def create_account(
    payload: SocialAccountCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    ctx: SiteContext = Depends(require_site_write),
) -> SocialAccount:
    acc = SocialAccount(**payload.model_dump(), user_id=current_user.id, site_id=ctx.site.id)
    db.add(acc)
    db.commit()
    db.refresh(acc)
    return acc


@router.patch("/accounts/{account_id}", response_model=SocialAccountOut, tags=["社交账号"])
def update_account(
    account_id: int,
    payload: SocialAccountUpdate,
    db: Session = Depends(get_db),
    ctx: SiteContext = Depends(require_site_write),
) -> SocialAccount:
    acc = (
        db.query(SocialAccount)
        .filter(SocialAccount.id == account_id, SocialAccount.site_id == ctx.site.id)
        .first()
    )
    if not acc:
        raise HTTPException(status_code=404, detail="账号不存在")
    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(acc, k, v)
    db.commit()
    db.refresh(acc)
    return acc


@router.delete("/accounts/{account_id}", status_code=status.HTTP_204_NO_CONTENT, tags=["社交账号"])
def delete_account(
    account_id: int,
    db: Session = Depends(get_db),
    ctx: SiteContext = Depends(require_site_write),
) -> None:
    acc = (
        db.query(SocialAccount)
        .filter(SocialAccount.id == account_id, SocialAccount.site_id == ctx.site.id)
        .first()
    )
    if not acc:
        raise HTTPException(status_code=404, detail="账号不存在")
    db.delete(acc)
    db.commit()


# ============ 发帖任务 ============
@router.get("/posts", response_model=list[SocialPostOut], tags=["发帖任务"])
def list_posts(
    status_filter: str | None = Query(default=None, alias="status"),
    account_id: int | None = None,
    article_id: int | None = None,
    page: int = Query(default=1, ge=1),
    size: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
    ctx: SiteContext = Depends(get_site_context),
) -> list[SocialPost]:
    q = (
        db.query(SocialPost)
        .join(SocialAccount, SocialPost.account_id == SocialAccount.id)
        .filter(SocialAccount.site_id == ctx.site.id)
    )
    if status_filter:
        q = q.filter(SocialPost.status == status_filter)
    if account_id:
        q = q.filter(SocialPost.account_id == account_id)
    if article_id:
        q = q.filter(SocialPost.article_id == article_id)
    posts = (
        q.order_by(SocialPost.created_at.desc())
        .offset((page - 1) * size)
        .limit(size)
        .all()
    )
    # 填充冗余展示字段（SocialPostOut 需要 platform / account_name / article_title）
    article_ids = {p.article_id for p in posts if p.article_id}
    article_titles: dict[int, str] = {}
    if article_ids:
        article_titles = {
            a.id: a.title
            for a in db.query(Article.id, Article.title)
            .filter(Article.id.in_(article_ids))
            .all()
        }
    for p in posts:
        if p.account:
            p.platform = p.account.platform
            p.account_name = p.account.account_name
        if p.article_id:
            p.article_title = article_titles.get(p.article_id)
    return posts


@router.post("/posts", response_model=SocialPostOut, status_code=status.HTTP_201_CREATED, tags=["发帖任务"])
def create_post(
    payload: SocialPostCreate,
    db: Session = Depends(get_db),
    ctx: SiteContext = Depends(require_site_write),
) -> SocialPost:
    # 验证账号归属
    acc = (
        db.query(SocialAccount)
        .filter(SocialAccount.id == payload.account_id, SocialAccount.site_id == ctx.site.id)
        .first()
    )
    if not acc:
        raise HTTPException(status_code=404, detail="社交账号不存在")
    post = SocialPost(**payload.model_dump())
    if post.scheduled_at:
        post.status = "scheduled"
    db.add(post)
    db.commit()
    db.refresh(post)

    # 如果是定时任务，投递到 Celery
    if post.scheduled_at:
        send_post_task.apply_async(args=[post.id], eta=post.scheduled_at)
    return post


@router.patch("/posts/{post_id}", response_model=SocialPostOut, tags=["发帖任务"])
def update_post(
    post_id: int,
    payload: SocialPostUpdate,
    db: Session = Depends(get_db),
    ctx: SiteContext = Depends(require_site_write),
) -> SocialPost:
    post = (
        db.query(SocialPost)
        .join(SocialAccount, SocialPost.account_id == SocialAccount.id)
        .filter(SocialPost.id == post_id, SocialAccount.site_id == ctx.site.id)
        .first()
    )
    if not post:
        raise HTTPException(status_code=404, detail="发帖任务不存在")
    if post.status in ("posted", "posting"):
        raise HTTPException(status_code=400, detail="已发送的任务不可修改")
    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(post, k, v)
    db.commit()
    db.refresh(post)
    return post


@router.delete("/posts/{post_id}", status_code=status.HTTP_204_NO_CONTENT, tags=["发帖任务"])
def delete_post(
    post_id: int,
    db: Session = Depends(get_db),
    ctx: SiteContext = Depends(require_site_write),
) -> None:
    post = (
        db.query(SocialPost)
        .join(SocialAccount, SocialPost.account_id == SocialAccount.id)
        .filter(SocialPost.id == post_id, SocialAccount.site_id == ctx.site.id)
        .first()
    )
    if not post:
        raise HTTPException(status_code=404, detail="发帖任务不存在")
    if post.status in ("posted", "posting"):
        raise HTTPException(status_code=400, detail="已发送的任务不可删除")
    db.delete(post)
    db.commit()


@router.post("/posts/{post_id}/send-now", response_model=SocialPostOut, tags=["发帖任务"])
def send_now(
    post_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> SocialPost:
    """立即发送（同步执行，不等 Celery）。"""
    post = (
        db.query(SocialPost)
        .join(SocialAccount, SocialPost.account_id == SocialAccount.id)
        .filter(SocialPost.id == post_id, SocialAccount.site_id == ctx.site.id)
        .first()
    )
    if not post:
        raise HTTPException(status_code=404, detail="发帖任务不存在")
    if post.status in ("posted", "posting"):
        raise HTTPException(status_code=400, detail="任务已发送")

    account = post.account
    try:
        client = get_platform_client(
            platform=account.platform,
            account_id=account.id,
            access_token=account.access_token,
            config=account.config,
        )
        payload = PublishPayload(
            title=post.title,
            summary=post.summary or "",
            image_url=post.image_url,
            external_url=post.external_url,
            hashtags=post.hashtags or [],
        )
        post.status = "posting"
        db.commit()
        result = client.publish_post(payload)
        post.status = "posted" if result.success else "failed"
        post.platform_post_id = result.platform_post_id
        post.posted_at = datetime.utcnow() if result.success else None
        if not result.success:
            post.error_message = result.error or "未知错误"
    except PlatformError as e:
        post.status = "failed"
        post.error_message = str(e)
        post.retry_count += 1
    db.commit()
    db.refresh(post)
    # 填充展示字段
    if post.account:
        post.platform = post.account.platform
        post.account_name = post.account.account_name
    if post.article_id:
        article = db.query(Article.title).filter(Article.id == post.article_id).first()
        post.article_title = article.title if article else None
    return post


# ============ 联动触发：文章发布时自动生成分发任务 ============
@router.post("/auto-distribute", response_model=AutoDistributeResponse, tags=["联动触发"])
def auto_distribute(
    payload: AutoDistributeRequest,
    db: Session = Depends(get_db),
    ctx: SiteContext = Depends(require_site_write),
) -> AutoDistributeResponse:
    """文章发布时自动为每个激活的社交账号生成发帖任务。

    流程：
    1. 取文章 + 关键词
    2. 取用户所有激活账号
    3. 调用 AI 生成各平台差异化文案
    4. 为每账号创建 SocialPost 记录（scheduled_at = now + delay_minutes）
    5. 投递到 Celery 定时发送
    """
    article = (
        db.query(Article)
        .filter(Article.id == payload.article_id, Article.site_id == ctx.site.id)
        .first()
    )
    if not article:
        raise HTTPException(status_code=404, detail="文章不存在")

    accounts = (
        db.query(SocialAccount)
        .filter(SocialAccount.site_id == ctx.site.id, SocialAccount.is_active == True)
        .all()
    )
    if not accounts:
        return AutoDistributeResponse(article_id=payload.article_id)

    # 取关键词
    keyword = ""
    if article.keyword:
        keyword = article.keyword.keyword

    # AI 生成各平台文案
    created_posts: list[SocialPost] = []
    skipped: list[str] = []
    schedule_time = datetime.utcnow() + timedelta(minutes=payload.delay_minutes)

    try:
        client = get_ai_client()
    except DeepSeekError:
        # AI 不可用，回退到通用摘要
        client = None

    for idx, acc in enumerate(accounts):
        # 错峰：每账号延迟 5 分钟
        eta = schedule_time + timedelta(minutes=idx * 5)
        platform_label = _platform_label(acc.platform)

        if client:
            try:
                copy = client.generate_social_copy(
                    platform=platform_label,
                    article_title=article.title,
                    keyword=keyword or article.title,
                    summary=(article.meta_description or (article.content or "")[:200]),
                )
                title = copy.title
                summary = copy.summary
                hashtags = copy.hashtags
            except DeepSeekError:
                title = article.title
                summary = article.meta_description or (article.content or "")[:150]
                hashtags = []
        else:
            title = article.title
            summary = article.meta_description or (article.content or "")[:150]
            hashtags = []

        post = SocialPost(
            article_id=article.id,
            account_id=acc.id,
            title=title,
            summary=summary,
            image_url=article.cover_image_url,
            external_url=article.target_url,
            hashtags=hashtags,
            scheduled_at=eta,
            status="scheduled",
        )
        db.add(post)
        db.commit()
        db.refresh(post)
        # 填充展示字段
        post.platform = acc.platform
        post.account_name = acc.account_name
        post.article_title = article.title
        created_posts.append(post)

        # 投递 Celery 定时任务
        send_post_task.apply_async(args=[post.id], eta=eta)

    return AutoDistributeResponse(
        article_id=payload.article_id,
        created_posts=created_posts,
        skipped_accounts=skipped,
    )


def _platform_label(platform: str) -> str:
    """数据库 platform -> AI  用的平台名。"""
    return {
        "pulseforge": "PulseForge",
        "linkedin": "LinkedIn",
        "twitter": "Twitter/X",
        "facebook": "Facebook",
        "reddit": "Reddit",
    }.get(platform.lower(), platform)
