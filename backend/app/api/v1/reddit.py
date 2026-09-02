from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.api.deps import SiteContext, get_current_user, get_site_context, require_site_write
from app.core.database import get_db
from app.models.reddit import RedditComment, RedditPost
from app.models.user import User
from app.schemas.reddit import (
    RedditCommentBatchGenerateIn,
    RedditCommentGenerateIn,
    RedditCommentOut,
    RedditCommentUpdate,
    RedditDiscoverItem,
    RedditDiscoverMeta,
    RedditDiscoverResponse,
    RedditOAuthStartOut,
    RedditPostGenerateIn,
    RedditPostOut,
    RedditPostUpdate,
    RedditStatusOut,
    RedditAccountOut,
)
from app.services.ai_writer import DeepSeekError, get_ai_client
from app.services.reddit_client import (
    RedditApiError,
    get_reddit_client_for_account,
    normalize_subreddit,
    parse_reddit_post_url,
)
from app.services import reddit_oauth

router = APIRouter()

_EDITABLE_STATUSES = {"draft", "pending_review", "approved"}


def _site_url_from_domain(domain: str) -> str:
    d = domain.strip()
    if not d:
        return ""
    if d.startswith("http://") or d.startswith("https://"):
        return d.rstrip("/")
    return f"https://{d.rstrip('/')}"


def _fill_post_out(post: RedditPost) -> RedditPost:
    if post.account:
        post.account_name = post.account.account_name
    return post


def _fill_comment_out(comment: RedditComment) -> RedditComment:
    if comment.account:
        comment.account_name = comment.account.account_name
    return comment


@router.get("/status", response_model=RedditStatusOut)
def reddit_status(
    db: Session = Depends(get_db),
    ctx: SiteContext = Depends(get_site_context),
) -> RedditStatusOut:
    accounts = reddit_oauth.list_reddit_accounts(db, ctx.site.id)
    connected = any(a.access_token for a in accounts)
    return RedditStatusOut(
        configured=reddit_oauth.is_reddit_configured(),
        connected=connected,
        accounts=[RedditAccountOut.model_validate(a) for a in accounts],
    )


@router.get("/oauth/start", response_model=RedditOAuthStartOut)
def oauth_start(
    current_user: User = Depends(get_current_user),
    ctx: SiteContext = Depends(require_site_write),
) -> RedditOAuthStartOut:
    try:
        auth_url = reddit_oauth.get_authorization_url(current_user.id, ctx.site.id)
    except reddit_oauth.RedditNotConfiguredError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return RedditOAuthStartOut(auth_url=auth_url)


@router.get("/oauth/callback")
def oauth_callback(
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    db: Session = Depends(get_db),
):
    if error:
        return RedirectResponse(reddit_oauth.frontend_redirect({"reddit": "error", "message": error}))
    if not code or not state:
        return RedirectResponse(reddit_oauth.frontend_redirect({"reddit": "error", "message": "missing_code"}))
    try:
        reddit_oauth.complete_oauth_callback(db, code, state)
    except (ValueError, RedditApiError) as exc:
        return RedirectResponse(
            reddit_oauth.frontend_redirect({"reddit": "error", "message": str(exc)[:200]})
        )
    return RedirectResponse(reddit_oauth.frontend_redirect({"reddit": "connected"}))


# ============ Posts ============
@router.post("/posts/generate", response_model=RedditPostOut, status_code=status.HTTP_201_CREATED)
def generate_post(
    payload: RedditPostGenerateIn,
    db: Session = Depends(get_db),
    ctx: SiteContext = Depends(require_site_write),
) -> RedditPost:
    account = reddit_oauth.get_site_account(db, ctx.site.id, payload.account_id)
    if not account:
        raise HTTPException(status_code=404, detail="Reddit 账号不存在")

    site_url = None
    if payload.include_site_url and payload.post_type == "experience":
        site_url = _site_url_from_domain(ctx.site.domain)

    try:
        ai = get_ai_client()
        generated = ai.generate_reddit_post(
            post_type=payload.post_type,
            subreddit=payload.subreddit,
            keyword=payload.keyword,
            site_url=site_url,
        )
    except DeepSeekError as exc:
        raise HTTPException(status_code=502, detail=f"AI 服务不可用：{exc}") from exc

    post = RedditPost(
        site_id=ctx.site.id,
        account_id=account.id,
        post_type=payload.post_type,
        subreddit=normalize_subreddit(payload.subreddit),
        keyword=payload.keyword,
        title=generated["title"],
        body=generated["body"],
        site_url=site_url,
        status="pending_review",
    )
    db.add(post)
    db.commit()
    db.refresh(post)
    return _fill_post_out(post)


@router.get("/posts", response_model=list[RedditPostOut])
def list_posts(
    status_filter: str | None = Query(default=None, alias="status"),
    db: Session = Depends(get_db),
    ctx: SiteContext = Depends(get_site_context),
) -> list[RedditPost]:
    q = db.query(RedditPost).filter(RedditPost.site_id == ctx.site.id)
    if status_filter:
        q = q.filter(RedditPost.status == status_filter)
    posts = q.order_by(RedditPost.created_at.desc()).limit(100).all()
    return [_fill_post_out(p) for p in posts]


@router.patch("/posts/{post_id}", response_model=RedditPostOut)
def update_post(
    post_id: int,
    payload: RedditPostUpdate,
    db: Session = Depends(get_db),
    ctx: SiteContext = Depends(require_site_write),
) -> RedditPost:
    post = db.query(RedditPost).filter(RedditPost.id == post_id, RedditPost.site_id == ctx.site.id).first()
    if not post:
        raise HTTPException(status_code=404, detail="发帖任务不存在")
    if post.status not in _EDITABLE_STATUSES:
        raise HTTPException(status_code=400, detail="当前状态不可编辑")
    for k, v in payload.model_dump(exclude_unset=True).items():
        if k == "subreddit" and v:
            v = normalize_subreddit(v)
        setattr(post, k, v)
    db.commit()
    db.refresh(post)
    return _fill_post_out(post)


@router.post("/posts/{post_id}/approve", response_model=RedditPostOut)
def approve_post(
    post_id: int,
    db: Session = Depends(get_db),
    ctx: SiteContext = Depends(require_site_write),
) -> RedditPost:
    post = db.query(RedditPost).filter(RedditPost.id == post_id, RedditPost.site_id == ctx.site.id).first()
    if not post:
        raise HTTPException(status_code=404, detail="发帖任务不存在")
    if post.status not in ("pending_review", "draft", "approved"):
        raise HTTPException(status_code=400, detail="当前状态不可批准")
    post.status = "approved"
    db.commit()
    db.refresh(post)
    return _fill_post_out(post)


@router.post("/posts/{post_id}/reject", response_model=RedditPostOut)
def reject_post(
    post_id: int,
    db: Session = Depends(get_db),
    ctx: SiteContext = Depends(require_site_write),
) -> RedditPost:
    post = db.query(RedditPost).filter(RedditPost.id == post_id, RedditPost.site_id == ctx.site.id).first()
    if not post:
        raise HTTPException(status_code=404, detail="发帖任务不存在")
    if post.status in ("posted", "posting"):
        raise HTTPException(status_code=400, detail="已发布的任务不可拒绝")
    post.status = "rejected"
    db.commit()
    db.refresh(post)
    return _fill_post_out(post)


@router.post("/posts/{post_id}/publish", response_model=RedditPostOut)
def publish_post(
    post_id: int,
    db: Session = Depends(get_db),
    ctx: SiteContext = Depends(require_site_write),
) -> RedditPost:
    post = db.query(RedditPost).filter(RedditPost.id == post_id, RedditPost.site_id == ctx.site.id).first()
    if not post:
        raise HTTPException(status_code=404, detail="发帖任务不存在")
    if post.status != "approved":
        raise HTTPException(status_code=400, detail="请先批准后再发布")
    account = post.account
    if not account:
        raise HTTPException(status_code=400, detail="关联账号不存在")

    post.status = "posting"
    db.commit()
    try:
        client = get_reddit_client_for_account(account)
        result = client.submit_post(post.subreddit, post.title, post.body)
        post.status = "posted"
        post.reddit_post_id = result.get("name") or result.get("id")
        permalink = result.get("permalink", "")
        if permalink and not permalink.startswith("http"):
            permalink = f"https://www.reddit.com{permalink}"
        post.reddit_permalink = permalink or None
        post.error_message = None
    except RedditApiError as exc:
        post.status = "failed"
        post.error_message = str(exc)
    db.commit()
    db.refresh(post)
    return _fill_post_out(post)


# ============ Comments ============
def _generate_comment_for_url(
    db: Session,
    ctx: SiteContext,
    account_id: int,
    target_post_url: str,
    *,
    include_site_url: bool,
    discover_source: str,
    keyword: str | None = None,
) -> RedditComment:
    account = reddit_oauth.get_site_account(db, ctx.site.id, account_id)
    if not account:
        raise HTTPException(status_code=404, detail="Reddit 账号不存在")
    try:
        thing_id, subreddit = parse_reddit_post_url(target_post_url)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    client = get_reddit_client_for_account(account)
    try:
        context = client.get_post_context(thing_id)
    except RedditApiError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    site_url = _site_url_from_domain(ctx.site.domain) if include_site_url else None
    try:
        ai = get_ai_client()
        body = ai.generate_reddit_comment(
            post_title=context["title"],
            post_body=context["body"],
            subreddit=context.get("subreddit") or subreddit,
            site_url=site_url,
        )
    except DeepSeekError as exc:
        raise HTTPException(status_code=502, detail=f"AI 服务不可用：{exc}") from exc

    comment = RedditComment(
        site_id=ctx.site.id,
        account_id=account.id,
        target_post_url=target_post_url,
        target_thing_id=thing_id,
        subreddit=context.get("subreddit") or subreddit,
        target_post_title=context.get("title"),
        body=body,
        keyword=keyword,
        discover_source=discover_source,
        status="pending_review",
    )
    db.add(comment)
    db.commit()
    db.refresh(comment)
    return _fill_comment_out(comment)


@router.post("/comments/generate", response_model=RedditCommentOut, status_code=status.HTTP_201_CREATED)
def generate_comment(
    payload: RedditCommentGenerateIn,
    db: Session = Depends(get_db),
    ctx: SiteContext = Depends(require_site_write),
) -> RedditComment:
    return _generate_comment_for_url(
        db,
        ctx,
        payload.account_id,
        payload.target_post_url,
        include_site_url=payload.include_site_url,
        discover_source="manual_url",
    )


@router.post("/comments/generate-batch", response_model=list[RedditCommentOut])
def generate_comment_batch(
    payload: RedditCommentBatchGenerateIn,
    db: Session = Depends(get_db),
    ctx: SiteContext = Depends(require_site_write),
) -> list[RedditComment]:
    created: list[RedditComment] = []
    for url in payload.post_urls:
        comment = _generate_comment_for_url(
            db,
            ctx,
            payload.account_id,
            url,
            include_site_url=payload.include_site_url,
            discover_source="keyword_search",
            keyword=payload.keyword,
        )
        created.append(comment)
    return created


@router.get("/comments", response_model=list[RedditCommentOut])
def list_comments(
    status_filter: str | None = Query(default=None, alias="status"),
    db: Session = Depends(get_db),
    ctx: SiteContext = Depends(get_site_context),
) -> list[RedditComment]:
    q = db.query(RedditComment).filter(RedditComment.site_id == ctx.site.id)
    if status_filter:
        q = q.filter(RedditComment.status == status_filter)
    comments = q.order_by(RedditComment.created_at.desc()).limit(100).all()
    return [_fill_comment_out(c) for c in comments]


@router.patch("/comments/{comment_id}", response_model=RedditCommentOut)
def update_comment(
    comment_id: int,
    payload: RedditCommentUpdate,
    db: Session = Depends(get_db),
    ctx: SiteContext = Depends(require_site_write),
) -> RedditComment:
    comment = (
        db.query(RedditComment)
        .filter(RedditComment.id == comment_id, RedditComment.site_id == ctx.site.id)
        .first()
    )
    if not comment:
        raise HTTPException(status_code=404, detail="评论任务不存在")
    if comment.status not in _EDITABLE_STATUSES:
        raise HTTPException(status_code=400, detail="当前状态不可编辑")
    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(comment, k, v)
    db.commit()
    db.refresh(comment)
    return _fill_comment_out(comment)


@router.post("/comments/{comment_id}/approve", response_model=RedditCommentOut)
def approve_comment(
    comment_id: int,
    db: Session = Depends(get_db),
    ctx: SiteContext = Depends(require_site_write),
) -> RedditComment:
    comment = (
        db.query(RedditComment)
        .filter(RedditComment.id == comment_id, RedditComment.site_id == ctx.site.id)
        .first()
    )
    if not comment:
        raise HTTPException(status_code=404, detail="评论任务不存在")
    if comment.status not in ("pending_review", "draft", "approved"):
        raise HTTPException(status_code=400, detail="当前状态不可批准")
    comment.status = "approved"
    db.commit()
    db.refresh(comment)
    return _fill_comment_out(comment)


@router.post("/comments/{comment_id}/reject", response_model=RedditCommentOut)
def reject_comment(
    comment_id: int,
    db: Session = Depends(get_db),
    ctx: SiteContext = Depends(require_site_write),
) -> RedditComment:
    comment = (
        db.query(RedditComment)
        .filter(RedditComment.id == comment_id, RedditComment.site_id == ctx.site.id)
        .first()
    )
    if not comment:
        raise HTTPException(status_code=404, detail="评论任务不存在")
    if comment.status in ("posted", "posting"):
        raise HTTPException(status_code=400, detail="已发布的评论不可拒绝")
    comment.status = "rejected"
    db.commit()
    db.refresh(comment)
    return _fill_comment_out(comment)


@router.post("/comments/{comment_id}/publish", response_model=RedditCommentOut)
def publish_comment(
    comment_id: int,
    db: Session = Depends(get_db),
    ctx: SiteContext = Depends(require_site_write),
) -> RedditComment:
    comment = (
        db.query(RedditComment)
        .filter(RedditComment.id == comment_id, RedditComment.site_id == ctx.site.id)
        .first()
    )
    if not comment:
        raise HTTPException(status_code=404, detail="评论任务不存在")
    if comment.status != "approved":
        raise HTTPException(status_code=400, detail="请先批准后再发布")
    account = comment.account
    if not account:
        raise HTTPException(status_code=400, detail="关联账号不存在")

    comment.status = "posting"
    db.commit()
    try:
        client = get_reddit_client_for_account(account)
        result = client.submit_comment(comment.target_thing_id, comment.body)
        comment.status = "posted"
        comment.reddit_comment_id = result.get("name") or result.get("id")
        comment.error_message = None
    except RedditApiError as exc:
        comment.status = "failed"
        comment.error_message = str(exc)
    db.commit()
    db.refresh(comment)
    return _fill_comment_out(comment)


# ============ Discover ============
@router.get("/discover/search", response_model=RedditDiscoverResponse)
def discover_search(
    subreddit: str = Query(..., min_length=1),
    keyword: str = Query(..., min_length=1),
    limit: int = Query(default=10, ge=1, le=25),
    auto_discover: bool = False,
    auto_queue: bool = False,
    account_id: int | None = None,
    include_site_url: bool = False,
    db: Session = Depends(get_db),
    ctx: SiteContext = Depends(require_site_write),
) -> RedditDiscoverResponse:
    account = reddit_oauth.list_reddit_accounts(db, ctx.site.id)
    client_account = account[0] if account else None
    if client_account is None:
        client = get_reddit_client_for_account(
            type("A", (), {"id": 0, "access_token": None, "refresh_token": None})()
        )
    else:
        client = get_reddit_client_for_account(client_account)

    try:
        raw_items = client.search_posts(subreddit, keyword, limit=limit)
    except RedditApiError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    items = [RedditDiscoverItem(**item) for item in raw_items]
    queued_count = 0

    if auto_discover and auto_queue and account_id and items:
        for item in items:
            try:
                _generate_comment_for_url(
                    db,
                    ctx,
                    account_id,
                    item.url,
                    include_site_url=include_site_url,
                    discover_source="auto_discover",
                    keyword=keyword,
                )
                queued_count += 1
            except HTTPException:
                continue

    return RedditDiscoverResponse(
        items=items,
        meta=RedditDiscoverMeta(auto_mode=auto_discover, queued_count=queued_count),
    )
