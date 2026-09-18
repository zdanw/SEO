"""社交分发 API：账号配置 + 发帖任务 CRUD + 立即发送。"""
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.orm import Session

from app.api.deps import SiteContext, get_current_user, get_site_context, require_site_write
from app.core.database import get_db
from app.models.user import User
from app.models.social import SocialAccount, SocialPost
from app.schemas.social import (
    SocialAccountCreate, SocialAccountUpdate, SocialAccountOut,
    SocialPostCreate, SocialPostUpdate, SocialPostOut,
)
from app.services.pulseforge_client import (
    get_platform_client, PublishPayload, PlatformError,
)
from app.tasks.social_tasks import send_post_task

router = APIRouter()


def _attach_account_fields(post: SocialPost) -> SocialPost:
    if post.account:
        post.platform = post.account.platform
        post.account_name = post.account.account_name
    return post


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


@router.get("/posts", response_model=list[SocialPostOut], tags=["发帖任务"])
def list_posts(
    status_filter: str | None = Query(default=None, alias="status"),
    account_id: int | None = None,
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
    posts = (
        q.order_by(SocialPost.created_at.desc())
        .offset((page - 1) * size)
        .limit(size)
        .all()
    )
    for p in posts:
        _attach_account_fields(p)
    return posts


@router.post("/posts", response_model=SocialPostOut, status_code=status.HTTP_201_CREATED, tags=["发帖任务"])
def create_post(
    payload: SocialPostCreate,
    db: Session = Depends(get_db),
    ctx: SiteContext = Depends(require_site_write),
) -> SocialPost:
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

    if post.scheduled_at:
        send_post_task.apply_async(args=[post.id], eta=post.scheduled_at)
    return _attach_account_fields(post)


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
    return _attach_account_fields(post)


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
    ctx: SiteContext = Depends(require_site_write),
    current_user: User = Depends(get_current_user),
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
    return _attach_account_fields(post)
