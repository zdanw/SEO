from __future__ import annotations

from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from fastapi.responses import RedirectResponse
from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import SiteContext, get_current_user, get_site_context, require_site_write
from app.core.database import get_db
from app.models.reddit import (
    RedditAccountProfile,
    RedditBrand,
    RedditComment,
    RedditCommunity,
    RedditPost,
    RedditPostMetric,
    RedditProduct,
    RedditProductKeyword,
)
from app.models.user import User
from app.models.zernio_key import ZernioApiKey
from app.schemas.reddit import (
    RedditAccountProfileUpdate,
    RedditBrandIn,
    RedditBrandOut,
    RedditBrandUpdate,
    RedditCommentBatchGenerateIn,
    RedditCommentGenerateIn,
    RedditCommentOut,
    RedditCommentUpdate,
    RedditCommunityCreate,
    RedditCommunityOut,
    RedditCommunityUpdate,
    RedditCommunitySuggestIn,
    RedditCommunitySuggestOut,
    RedditDiscoverItem,
    RedditDiscoverMeta,
    RedditDiscoverResponse,
    RedditEngageCommentOut,
    RedditEngageCommentsOut,
    RedditEngageFeedOut,
    RedditMetricOut,
    RedditOAuthStartOut,
    RedditOverviewOut,
    RedditPostGenerateIn,
    RedditPostOut,
    RedditProductIn,
    RedditProductOut,
    RedditProductUpdate,
    RedditScheduleIn,
    RedditSmartDiscoverIn,
    RedditPostUpdate,
    RedditStatusOut,
    RedditAccountOut,
    RedditVoteIn,
    RedditVoteOut,
    ZernioKeyCreate,
    ZernioKeyOut,
    ZernioKeyUpdate,
)
from app.services.ai_writer import DeepSeekError, get_ai_client
from app.services.reddit_client import (
    RedditApiError,
    get_reddit_client_for_account,
    normalize_subreddit,
    parse_reddit_post_url,
)
from app.services.reddit_content import generate_comment_pipeline
from app.services.reddit_community_verify import (
    apply_verify_to_community,
    verify_subreddit,
    verify_subreddits_with_feed_llm,
)
from app.services.reddit_discover import smart_discover_for_account, suggest_persona_subreddits
from app.services.reddit_mix import (
    MixQuotaExceeded,
    count_mix_window,
    resolve_intent,
    resolve_post_intent,
)
from app.services.reddit_persona import parse_persona
from app.services import reddit_oauth
from app.services.reddit_publish import publish_comment_now, publish_post_now
from app.services.reddit_risk import (
    RiskViolation,
    apply_role_defaults,
    clear_warning as clear_account_warning,
    ensure_karma_stage_consistency,
    get_or_create_profile,
    get_account_daily_usage,
)
from app.services.zernio_client import ZernioError
from app.services.zernio_keys import is_zernio_ready, list_all_keys, list_enabled_keys, mask_api_key

router = APIRouter()

_EDITABLE_STATUSES = {"draft", "pending_review", "approved", "failed"}


def _risk_http(exc: RiskViolation) -> HTTPException:
    """风控拦截 → 409，返回阻断与提示明细。"""
    detail: dict = {"message": "发布被风控规则拦截", "errors": exc.errors, "warnings": exc.warnings}
    return HTTPException(status_code=409, detail=detail)


def _mix_http(exc: MixQuotaExceeded) -> HTTPException:
    return HTTPException(status_code=409, detail=str(exc))


def _purpose_for_subreddit(db: Session, site_id: int, subreddit: str) -> str | None:
    name = normalize_subreddit(subreddit)
    row = (
        db.query(RedditCommunity)
        .filter(RedditCommunity.site_id == site_id, RedditCommunity.name == name)
        .first()
    )
    return row.purpose if row else None


def _persona_prompt_for(db: Session, site_id: int, account_id: int) -> str:
    profile = (
        db.query(RedditAccountProfile)
        .filter(RedditAccountProfile.site_id == site_id, RedditAccountProfile.account_id == account_id)
        .first()
    )
    if not profile:
        return ""
    return parse_persona(profile.persona, config=profile.persona_config).to_prompt()


def _brief_from_product(brand: RedditBrand, product: RedditProduct) -> dict:
    return {
        "brand": brand.name or "",
        "product": product.name or "",
        "category": product.category or "",
        "talking_points": product.talking_points or [],
        "competitors": [],
        "never_claim": "",
    }


def _require_brand_product(
    db: Session,
    site_id: int,
    brand_id: int | None,
    product_id: int | None,
) -> tuple[RedditBrand, RedditProduct]:
    if not brand_id or not product_id:
        raise HTTPException(status_code=400, detail="产品向评论请选择品牌和产品")
    brand = (
        db.query(RedditBrand)
        .filter(RedditBrand.id == brand_id, RedditBrand.site_id == site_id, RedditBrand.is_active.is_(True))
        .first()
    )
    if not brand:
        raise HTTPException(status_code=404, detail="品牌不存在或已停用")
    product = (
        db.query(RedditProduct)
        .filter(
            RedditProduct.id == product_id,
            RedditProduct.brand_id == brand.id,
            RedditProduct.is_active.is_(True),
        )
        .first()
    )
    if not product:
        raise HTTPException(status_code=404, detail="产品不存在或不属于该品牌")
    return brand, product


def _fill_post_out(post: RedditPost) -> RedditPost:
    if post.account:
        post.account_name = reddit_oauth.display_reddit_name(post.account.account_name)
    return post


def _fill_comment_out(db: Session, comment: RedditComment) -> RedditComment:
    if comment.account:
        comment.account_name = reddit_oauth.display_reddit_name(comment.account.account_name)
    if comment.brand_id:
        brand = db.query(RedditBrand).filter(RedditBrand.id == comment.brand_id).first()
        comment.brand_name = brand.name if brand else None
    else:
        comment.brand_name = None
    if comment.product_id:
        product = db.query(RedditProduct).filter(RedditProduct.id == comment.product_id).first()
        comment.product_name = product.name if product else None
    else:
        comment.product_name = None
    return comment


def _product_out(product: RedditProduct) -> RedditProductOut:
    communities = sorted(product.communities or [], key=lambda c: c.id)
    keywords = [r.keyword for r in (product.keyword_rows or [])]
    return RedditProductOut(
        id=product.id,
        brand_id=product.brand_id,
        name=product.name,
        category=product.category or "",
        talking_points=product.talking_points or [],
        keywords=keywords,
        is_active=product.is_active,
        community_ids=[c.id for c in communities],
        community_names=[c.name for c in communities],
        created_at=product.created_at,
        updated_at=product.updated_at,
    )


def _set_product_keywords(product: RedditProduct, keywords: list[str] | None) -> None:
    from app.services.reddit_discover import normalize_product_keywords

    cleaned = normalize_product_keywords(keywords)
    product.keyword_rows = [
        RedditProductKeyword(keyword=kw) for kw in cleaned
    ]


def _set_product_communities(
    db: Session,
    *,
    site_id: int,
    product: RedditProduct,
    community_ids: list[int],
) -> None:
    ids = sorted({int(i) for i in community_ids if i})
    if not ids:
        product.communities = []
        return
    rows = (
        db.query(RedditCommunity)
        .filter(
            RedditCommunity.site_id == site_id,
            RedditCommunity.id.in_(ids),
            RedditCommunity.purpose == "promo",
        )
        .all()
    )
    if len(rows) != len(ids):
        found = {r.id for r in rows}
        missing = [i for i in ids if i not in found]
        raise HTTPException(status_code=400, detail=f"只能绑定本站产品社区，无效 id: {missing}")
    product.communities = rows


def _brand_out(brand: RedditBrand) -> RedditBrandOut:
    products = [
        _product_out(p)
        for p in sorted(brand.products or [], key=lambda x: x.id)
    ]
    return RedditBrandOut(
        id=brand.id,
        site_id=brand.site_id,
        name=brand.name,
        is_active=brand.is_active,
        products=products,
        created_at=brand.created_at,
        updated_at=brand.updated_at,
    )


def _raise_reddit_upstream(exc: RedditApiError) -> None:
    status_code = exc.status_code or 502
    retry_after = exc.retry_after
    if status_code == 429:
        # Zernio/Reddit 共享配额：搜索、拉评论、投票都可能 429，文案勿写死成「搜索」
        upstream = str(exc).strip()
        if retry_after:
            minutes = max(1, (retry_after + 59) // 60)
            detail = f"Reddit 请求过于频繁（上游限流），请约 {minutes} 分钟后再试"
        else:
            detail = "Reddit 请求过于频繁（上游限流），请稍后再试"
        if upstream and "429" not in upstream:
            detail = f"{detail}：{upstream[:160]}"
        headers = {"Retry-After": str(retry_after)} if retry_after else None
        raise HTTPException(status_code=429, detail=detail, headers=headers)
    if 400 <= status_code < 500:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    raise HTTPException(status_code=502, detail=str(exc)) from exc


def _site_url_from_domain(domain: str) -> str:
    d = domain.strip()
    if not d:
        return ""
    if d.startswith("http://") or d.startswith("https://"):
        return d.rstrip("/")
    return f"https://{d.rstrip('/')}"


def _key_out(row: ZernioApiKey) -> ZernioKeyOut:
    return ZernioKeyOut(
        id=row.id,
        label=row.label,
        api_key_masked=mask_api_key(row.api_key),
        profile_id=row.profile_id,
        is_enabled=row.is_enabled,
        created_at=row.created_at,
    )


def _accounts_out(db: Session, accounts) -> list[RedditAccountOut]:
    labels = {k.id: k.label for k in list_all_keys(db)}
    profiles = {
        p.account_id: p
        for p in db.query(RedditAccountProfile)
        .filter(RedditAccountProfile.account_id.in_([a.id for a in accounts] or [0]))
        .all()
    }
    out: list[RedditAccountOut] = []
    for acc in accounts:
        cfg = acc.config if isinstance(acc.config, dict) else {}
        raw_kid = cfg.get("zernio_key_id")
        kid: int | None = None
        if raw_kid not in (None, ""):
            try:
                kid = int(raw_kid)
            except (TypeError, ValueError):
                kid = None
        profile = profiles.get(acc.id)
        usage = get_account_daily_usage(db, acc.id)
        out.append(
            RedditAccountOut(
                id=acc.id,
                account_name=reddit_oauth.display_reddit_name(acc.account_name) or acc.account_name,
                is_active=acc.is_active,
                zernio_key_id=kid,
                zernio_key_label=labels.get(kid) if kid is not None else None,
                role=profile.role if profile else None,
                stage=profile.stage if profile else None,
                karma=profile.karma if profile else 0,
                persona=profile.persona if profile else None,
                persona_config=profile.persona_config if profile else None,
                daily_post_limit=profile.daily_post_limit if profile else 0,
                daily_comment_limit=profile.daily_comment_limit if profile else 0,
                risk_status=profile.risk_status if profile else "normal",
                risk_reason=profile.risk_reason if profile else None,
                posts_today=usage["posts"],
                comments_today=usage["comments"],
            )
        )
    return out


def _status_out(
    db: Session,
    accounts,
    sync_errors: list[str] | None = None,
) -> RedditStatusOut:
    return RedditStatusOut(
        configured=is_zernio_ready(db),
        connected=bool(accounts),
        accounts=_accounts_out(db, accounts),
        zernio_key_count=len(list_enabled_keys(db)),
        sync_errors=sync_errors or [],
    )


@router.get("/status", response_model=RedditStatusOut)
def reddit_status(
    db: Session = Depends(get_db),
    ctx: SiteContext = Depends(get_site_context),
) -> RedditStatusOut:
    return _status_out(db, reddit_oauth.list_reddit_accounts(db, ctx.site.id))


@router.post("/accounts/sync", response_model=RedditStatusOut)
def sync_accounts(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    ctx: SiteContext = Depends(require_site_write),
) -> RedditStatusOut:
    try:
        accounts, errors = reddit_oauth.sync_zernio_accounts(db, current_user.id, ctx.site.id)
    except ZernioError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return _status_out(db, accounts, errors)


@router.get("/zernio-keys", response_model=list[ZernioKeyOut])
def list_zernio_keys(
    db: Session = Depends(get_db),
    ctx: SiteContext = Depends(get_site_context),
) -> list[ZernioKeyOut]:
    return [_key_out(row) for row in list_all_keys(db)]


@router.post("/zernio-keys", response_model=ZernioKeyOut, status_code=status.HTTP_201_CREATED)
def create_zernio_key(
    payload: ZernioKeyCreate,
    db: Session = Depends(get_db),
    ctx: SiteContext = Depends(require_site_write),
) -> ZernioKeyOut:
    row = ZernioApiKey(
        label=payload.label.strip(),
        api_key=payload.api_key.strip(),
        profile_id=(payload.profile_id or "").strip() or None,
        is_enabled=True,
    )
    db.add(row)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="该 Zernio API Key 已存在") from exc
    db.refresh(row)
    return _key_out(row)


@router.patch("/zernio-keys/{key_id}", response_model=ZernioKeyOut)
def update_zernio_key(
    key_id: int,
    payload: ZernioKeyUpdate,
    db: Session = Depends(get_db),
    ctx: SiteContext = Depends(require_site_write),
) -> ZernioKeyOut:
    row = db.query(ZernioApiKey).filter(ZernioApiKey.id == key_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Zernio Key 不存在")
    if payload.label is not None:
        row.label = payload.label.strip()
    if payload.api_key is not None:
        row.api_key = payload.api_key.strip()
    if payload.profile_id is not None:
        row.profile_id = payload.profile_id.strip() or None
    if payload.is_enabled is not None:
        row.is_enabled = payload.is_enabled
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="该 Zernio API Key 已存在") from exc
    db.refresh(row)
    return _key_out(row)


@router.delete("/zernio-keys/{key_id}", status_code=status.HTTP_204_NO_CONTENT, response_class=Response)
def delete_zernio_key(
    key_id: int,
    db: Session = Depends(get_db),
    ctx: SiteContext = Depends(require_site_write),
):
    row = db.query(ZernioApiKey).filter(ZernioApiKey.id == key_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Zernio Key 不存在")
    db.delete(row)
    db.commit()
    reddit_oauth.deactivate_orphan_reddit_accounts(db, ctx.site.id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


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
    connected: str | None = None,
    error: str | None = None,
    message: str | None = None,
):
    """兼容把 redirect 指到后端的情况：原样转到前端，由前端带 JWT 同步账号。"""
    if error:
        return RedirectResponse(
            reddit_oauth.frontend_redirect({"reddit": "error", "message": error or message or ""})
        )
    params = {"reddit": "connected"}
    if connected:
        params["connected"] = connected
    return RedirectResponse(reddit_oauth.frontend_redirect(params))


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
    if payload.include_site_url and payload.post_type in {"pitfall", "guide"}:
        site_url = _site_url_from_domain(ctx.site.domain)

    purpose = _purpose_for_subreddit(db, ctx.site.id, payload.subreddit)
    intent = resolve_post_intent(
        post_type=payload.post_type,
        community_purpose=purpose,
        include_site_url=bool(site_url),
    )

    persona_prompt = _persona_prompt_for(db, ctx.site.id, account.id)
    product_brief = None
    keyword = (payload.keyword or "").strip()
    recent_titles = [
        row[0]
        for row in (
            db.query(RedditPost.title)
            .filter(
                RedditPost.account_id == account.id,
                RedditPost.post_type == payload.post_type,
            )
            .order_by(RedditPost.id.desc())
            .limit(8)
            .all()
        )
        if row[0]
    ]

    try:
        ai = get_ai_client()
        generated = ai.generate_reddit_post(
            post_type=payload.post_type,
            subreddit=payload.subreddit,
            keyword=keyword,
            site_url=site_url,
            persona_prompt=persona_prompt,
            product_brief=product_brief,
            avoid_titles=recent_titles,
        )
    except DeepSeekError as exc:
        raise HTTPException(status_code=502, detail=f"AI 服务不可用：{exc}") from exc

    post = RedditPost(
        site_id=ctx.site.id,
        account_id=account.id,
        post_type=payload.post_type,
        subreddit=normalize_subreddit(payload.subreddit),
        keyword=keyword,
        title=generated["title"],
        body=generated["body"],
        site_url=site_url,
        status="pending_review",
        content_intent=intent,
    )
    db.add(post)
    db.commit()
    db.refresh(post)
    return _fill_post_out(post)


@router.get("/posts", response_model=list[RedditPostOut])
def list_posts(
    status_filter: str | None = Query(default=None, alias="status"),
    account_id: int | None = None,
    db: Session = Depends(get_db),
    ctx: SiteContext = Depends(get_site_context),
) -> list[RedditPost]:
    q = db.query(RedditPost).filter(RedditPost.site_id == ctx.site.id)
    if account_id:
        q = q.filter(RedditPost.account_id == account_id)
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


@router.delete("/posts/{post_id}", status_code=204)
def delete_post(
    post_id: int,
    db: Session = Depends(get_db),
    ctx: SiteContext = Depends(require_site_write),
):
    post = db.query(RedditPost).filter(RedditPost.id == post_id, RedditPost.site_id == ctx.site.id).first()
    if not post:
        raise HTTPException(status_code=404, detail="发帖任务不存在")
    if post.status == "posting":
        raise HTTPException(status_code=400, detail="发布中的任务不可删除")
    db.delete(post)
    db.commit()


@router.post("/posts/{post_id}/publish", response_model=RedditPostOut)
def publish_post(
    post_id: int,
    db: Session = Depends(get_db),
    ctx: SiteContext = Depends(require_site_write),
) -> RedditPost:
    post = db.query(RedditPost).filter(RedditPost.id == post_id, RedditPost.site_id == ctx.site.id).first()
    if not post:
        raise HTTPException(status_code=404, detail="发帖任务不存在")
    try:
        post = publish_post_now(db, post)
    except MixQuotaExceeded as exc:
        raise _mix_http(exc) from exc
    except RiskViolation as exc:
        raise _risk_http(exc) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _fill_post_out(post)


@router.post("/posts/{post_id}/schedule", response_model=RedditPostOut)
def schedule_post(
    post_id: int,
    payload: RedditScheduleIn,
    db: Session = Depends(get_db),
    ctx: SiteContext = Depends(require_site_write),
) -> RedditPost:
    """批准后的帖子设置定时发布时间，由 Celery Beat 每 15 分钟自动扫描发布。"""
    post = db.query(RedditPost).filter(RedditPost.id == post_id, RedditPost.site_id == ctx.site.id).first()
    if not post:
        raise HTTPException(status_code=404, detail="发帖任务不存在")
    if post.status != "approved":
        raise HTTPException(status_code=400, detail="请先批准后再设置定时发布")
    if payload.scheduled_at <= datetime.utcnow():
        raise HTTPException(status_code=400, detail="定时时间必须晚于当前时间")
    post.scheduled_at = payload.scheduled_at
    db.commit()
    db.refresh(post)
    return _fill_post_out(post)


@router.post("/posts/{post_id}/schedule/cancel", response_model=RedditPostOut)
def cancel_post_schedule(
    post_id: int,
    db: Session = Depends(get_db),
    ctx: SiteContext = Depends(require_site_write),
) -> RedditPost:
    post = db.query(RedditPost).filter(RedditPost.id == post_id, RedditPost.site_id == ctx.site.id).first()
    if not post:
        raise HTTPException(status_code=404, detail="发帖任务不存在")
    post.scheduled_at = None
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
    known_title: str | None = None,
    known_body: str | None = None,
    content_intent: str | None = None,
    brand_id: int | None = None,
    product_id: int | None = None,
) -> RedditComment:
    account = reddit_oauth.get_site_account(db, ctx.site.id, account_id)
    if not account:
        raise HTTPException(status_code=404, detail="Reddit 账号不存在")
    try:
        thing_id, subreddit = parse_reddit_post_url(target_post_url)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    title = (known_title or "").strip()
    body = (known_body or "").strip()
    if not title and not body:
        client = get_reddit_client_for_account(account)
        try:
            context = client.get_post_context(thing_id, subreddit)
        except RedditApiError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        title = (context.get("title") or "").strip()
        body = (context.get("body") or "").strip()
        subreddit = context.get("subreddit") or subreddit

    if not title and not body:
        raise HTTPException(
            status_code=400,
            detail="无法获取目标帖标题/正文，请从搜索结果勾选生成，或换一条帖子重试",
        )

    site_url = _site_url_from_domain(ctx.site.domain) if include_site_url else None
    purpose = _purpose_for_subreddit(db, ctx.site.id, subreddit)
    intent = content_intent or resolve_intent(community_purpose=purpose, include_site_url=include_site_url)
    if intent == "casual":
        site_url = None
        include_site_url = False
    persona_prompt = _persona_prompt_for(db, ctx.site.id, account.id)
    product_brief = None
    resolved_brand_id = None
    resolved_product_id = None
    if intent == "promo":
        brand, product = _require_brand_product(db, ctx.site.id, brand_id, product_id)
        product_brief = _brief_from_product(brand, product)
        resolved_brand_id = brand.id
        resolved_product_id = product.id
    try:
        ai = get_ai_client()
        generated = generate_comment_pipeline(
            ai,
            post_title=title,
            post_body=body,
            subreddit=subreddit,
            intent=intent,
            persona_prompt=persona_prompt,
            product_brief=product_brief,
            site_url=site_url,
            seed=account.id + len(title),
        )
        comment_body = generated.body
        ai_risk = generated.ai_risk
    except DeepSeekError as exc:
        raise HTTPException(status_code=502, detail=f"AI 服务不可用：{exc}") from exc

    comment = RedditComment(
        site_id=ctx.site.id,
        account_id=account.id,
        target_post_url=target_post_url,
        target_thing_id=thing_id,
        subreddit=normalize_subreddit(subreddit),
        target_post_title=title or None,
        body=comment_body,
        keyword=keyword,
        discover_source=discover_source,
        content_intent=intent,
        ai_risk=ai_risk,
        brand_id=resolved_brand_id,
        product_id=resolved_product_id,
        status="pending_review",
    )
    db.add(comment)
    db.commit()
    db.refresh(comment)
    return _fill_comment_out(db, comment)


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
        known_title=payload.target_post_title,
        known_body=payload.target_post_body,
        content_intent=payload.content_intent,
        brand_id=payload.brand_id,
        product_id=payload.product_id,
    )


@router.post("/comments/generate-batch", response_model=list[RedditCommentOut])
def generate_comment_batch(
    payload: RedditCommentBatchGenerateIn,
    db: Session = Depends(get_db),
    ctx: SiteContext = Depends(require_site_write),
) -> list[RedditComment]:
    created: list[RedditComment] = []
    for post in payload.posts:
        comment = _generate_comment_for_url(
            db,
            ctx,
            payload.account_id,
            post.url,
            include_site_url=payload.include_site_url,
            discover_source="keyword_search",
            keyword=payload.keyword,
            known_title=post.title,
            known_body=post.body,
            content_intent=payload.content_intent,
            brand_id=payload.brand_id,
            product_id=payload.product_id,
        )
        created.append(comment)
    return created


@router.get("/comments", response_model=list[RedditCommentOut])
def list_comments(
    status_filter: str | None = Query(default=None, alias="status"),
    account_id: int | None = None,
    db: Session = Depends(get_db),
    ctx: SiteContext = Depends(get_site_context),
) -> list[RedditComment]:
    q = db.query(RedditComment).filter(RedditComment.site_id == ctx.site.id)
    if account_id:
        q = q.filter(RedditComment.account_id == account_id)
    if status_filter:
        q = q.filter(RedditComment.status == status_filter)
    comments = q.order_by(RedditComment.created_at.desc()).limit(100).all()
    return [_fill_comment_out(db, c) for c in comments]


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
    return _fill_comment_out(db, comment)


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
    return _fill_comment_out(db, comment)


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
    return _fill_comment_out(db, comment)


@router.delete("/comments/{comment_id}", status_code=204)
def delete_comment(
    comment_id: int,
    db: Session = Depends(get_db),
    ctx: SiteContext = Depends(require_site_write),
):
    comment = (
        db.query(RedditComment)
        .filter(RedditComment.id == comment_id, RedditComment.site_id == ctx.site.id)
        .first()
    )
    if not comment:
        raise HTTPException(status_code=404, detail="评论任务不存在")
    if comment.status == "posting":
        raise HTTPException(status_code=400, detail="发布中的评论不可删除")
    db.delete(comment)
    db.commit()


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
    try:
        comment = publish_comment_now(db, comment)
    except MixQuotaExceeded as exc:
        raise _mix_http(exc) from exc
    except RiskViolation as exc:
        raise _risk_http(exc) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _fill_comment_out(db, comment)


@router.post("/comments/{comment_id}/schedule", response_model=RedditCommentOut)
def schedule_comment(
    comment_id: int,
    payload: RedditScheduleIn,
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
        raise HTTPException(status_code=400, detail="请先批准后再设置定时发布")
    if payload.scheduled_at <= datetime.utcnow():
        raise HTTPException(status_code=400, detail="定时时间必须晚于当前时间")
    comment.scheduled_at = payload.scheduled_at
    db.commit()
    db.refresh(comment)
    return _fill_comment_out(db, comment)


@router.post("/comments/{comment_id}/schedule/cancel", response_model=RedditCommentOut)
def cancel_comment_schedule(
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
    comment.scheduled_at = None
    db.commit()
    db.refresh(comment)
    return _fill_comment_out(db, comment)


# ============ Engage (养号互动：拉帖/评论 + 点赞) ============
@router.get("/engage/feed", response_model=RedditEngageFeedOut)
def engage_feed(
    subreddit: str = Query(..., min_length=1),
    account_id: int = Query(...),
    limit: int = Query(default=15, ge=1, le=25),
    db: Session = Depends(get_db),
    ctx: SiteContext = Depends(require_site_write),
) -> RedditEngageFeedOut:
    account = reddit_oauth.get_site_account(db, ctx.site.id, account_id)
    if not account:
        raise HTTPException(status_code=404, detail="Reddit 账号不存在")
    client = get_reddit_client_for_account(account)
    try:
        raw = client.list_feed(subreddit, limit=limit)
    except RedditApiError as exc:
        _raise_reddit_upstream(exc)
    items = [
        RedditDiscoverItem(
            title=item.get("title") or "",
            url=item.get("url") or "",
            thing_id=item["thing_id"],
            subreddit=item.get("subreddit") or normalize_subreddit(subreddit),
            score=int(item.get("score") or 0),
            num_comments=int(item.get("num_comments") or 0),
            created_utc=int(item.get("created_utc") or 0),
            body=item.get("body") or "",
        )
        for item in raw
        if item.get("thing_id") and item.get("url")
    ]
    return RedditEngageFeedOut(items=items)


@router.get("/engage/comments", response_model=RedditEngageCommentsOut)
def engage_comments(
    thing_id: str = Query(..., min_length=3),
    subreddit: str = Query(default=""),
    account_id: int = Query(...),
    limit: int = Query(default=8, ge=1, le=15),
    db: Session = Depends(get_db),
    ctx: SiteContext = Depends(require_site_write),
) -> RedditEngageCommentsOut:
    account = reddit_oauth.get_site_account(db, ctx.site.id, account_id)
    if not account:
        raise HTTPException(status_code=404, detail="Reddit 账号不存在")
    client = get_reddit_client_for_account(account)
    try:
        raw = client.list_post_comments(thing_id, subreddit=subreddit, limit=limit)
    except RedditApiError as exc:
        _raise_reddit_upstream(exc)
    return RedditEngageCommentsOut(
        items=[
            RedditEngageCommentOut(
                thing_id=c["thing_id"],
                body=c.get("body") or "",
                author=c.get("author") or "",
                score=int(c.get("score") or 0),
                created_utc=int(c.get("created_utc") or 0),
                url=c.get("url") or "",
            )
            for c in raw
            if c.get("thing_id")
        ]
    )


@router.post("/engage/vote", response_model=RedditVoteOut)
def engage_vote(
    payload: RedditVoteIn,
    db: Session = Depends(get_db),
    ctx: SiteContext = Depends(require_site_write),
) -> RedditVoteOut:
    if payload.direction not in (1, 0, -1):
        raise HTTPException(status_code=400, detail="direction 必须是 1 / 0 / -1")
    account = reddit_oauth.get_site_account(db, ctx.site.id, payload.account_id)
    if not account:
        raise HTTPException(status_code=404, detail="Reddit 账号不存在")
    client = get_reddit_client_for_account(account)
    try:
        client.vote(payload.thing_id.strip(), payload.direction)
    except RedditApiError as exc:
        _raise_reddit_upstream(exc)
    return RedditVoteOut(ok=True, thing_id=payload.thing_id.strip(), direction=payload.direction)


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
    accounts = reddit_oauth.list_reddit_accounts(db, ctx.site.id)
    client_account = accounts[0] if accounts else None
    if account_id:
        picked = reddit_oauth.get_site_account(db, ctx.site.id, account_id)
        if picked:
            client_account = picked
    if client_account is None:
        if is_zernio_ready(db):
            raise HTTPException(status_code=400, detail="请先同步 Reddit 账号后再搜索")
        client = get_reddit_client_for_account(
            type("A", (), {"id": 0, "config": None, "access_token": None})()
        )
    else:
        client = get_reddit_client_for_account(client_account)

    try:
        raw_items = client.search_posts(subreddit, keyword, limit=limit)
    except RedditApiError as exc:
        _raise_reddit_upstream(exc)

    items = [
        RedditDiscoverItem(
            title=item["title"],
            url=item["url"],
            thing_id=item["thing_id"],
            subreddit=item["subreddit"],
            score=item.get("score", 0),
            num_comments=item.get("num_comments", 0),
            created_utc=item.get("created_utc", 0),
            body=item.get("body") or "",
        )
        for item in raw_items
        if item.get("thing_id") and item.get("url")
    ]
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
                    known_title=item.title,
                    known_body=item.body,
                )
                queued_count += 1
            except HTTPException:
                continue

    return RedditDiscoverResponse(
        items=items,
        meta=RedditDiscoverMeta(auto_mode=auto_discover, queued_count=queued_count),
    )


def _remaining_comment_slots(db: Session, site_id: int, account_id: int) -> int:
    profile = get_or_create_profile(db, site_id, account_id)
    day_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    used = (
        db.query(RedditComment)
        .filter(
            RedditComment.account_id == account_id,
            RedditComment.created_at >= day_start,
            RedditComment.status != "rejected",
        )
        .count()
    )
    return max(0, int(profile.daily_comment_limit or 0) - used)


@router.post("/discover/smart", response_model=RedditDiscoverResponse)
def smart_discover(
    payload: RedditSmartDiscoverIn,
    db: Session = Depends(get_db),
    ctx: SiteContext = Depends(require_site_write),
) -> RedditDiscoverResponse:
    account = reddit_oauth.get_site_account(db, ctx.site.id, payload.account_id)
    if not account:
        raise HTTPException(status_code=404, detail="Reddit 账号不存在")

    # 规范化并去重；选了产品社区则必须带品牌+产品（写产品向文案）
    selected: list[str] = []
    seen_sr: set[str] = set()
    has_promo = False
    for raw in payload.subreddits:
        sr = normalize_subreddit(raw)
        key = sr.lower()
        if not sr or key in seen_sr:
            continue
        seen_sr.add(key)
        selected.append(sr)
        if _purpose_for_subreddit(db, ctx.site.id, sr) == "promo":
            has_promo = True
    if not selected:
        raise HTTPException(status_code=400, detail="请至少选择一个评论社区")
    if has_promo:
        _require_brand_product(db, ctx.site.id, payload.brand_id, payload.product_id)

    remaining = min(len(selected), _remaining_comment_slots(db, ctx.site.id, account.id))
    if remaining <= 0:
        raise HTTPException(status_code=409, detail="今日评论额度已用完")

    client = get_reddit_client_for_account(account)

    def _search(subreddit: str, keyword: str, limit: int):
        # 带关键词走 search（无 search 时 Zernio 客户端会回退 feed + 关键词过滤）
        return client.search_posts(subreddit, keyword, limit=limit)

    def _generate(item: dict, intent: str) -> None:
        _generate_comment_for_url(
            db,
            ctx,
            account.id,
            item["url"],
            include_site_url=False,
            discover_source="auto_discover",
            keyword=item.get("title") or "",
            known_title=item.get("title"),
            known_body=item.get("body"),
            content_intent=intent,
            brand_id=payload.brand_id,
            product_id=payload.product_id,
        )

    result = smart_discover_for_account(
        db,
        site_id=ctx.site.id,
        account_id=account.id,
        generate_comment=_generate,
        search_posts=_search,
        remaining_slots=remaining,
        seed=account.id,
        product_id=payload.product_id,
        subreddits=selected,
    )
    queued = int(result.get("queued") or 0)
    last_error = str(result.get("last_error") or "")
    reason = str(result.get("reason") or "")
    if queued == 0:
        if reason == "no_communities":
            raise HTTPException(status_code=400, detail="请至少选择一个有效社区")
        if reason == "upstream" or int(result.get("errors") or 0) > 0:
            raise HTTPException(
                status_code=502,
                detail=last_error or "Zernio 未能拉取社区帖子，请确认 Reddit 账号已同步且 Key 有效",
            )
        if reason == "generate_failed":
            raise HTTPException(status_code=502, detail=last_error or "评论生成失败")
        raise HTTPException(
            status_code=409,
            detail="没有找到与所选产品相关、且 48 小时内可评论的讨论。请换社区、检查产品绑定，或稍后再试。",
        )
    return RedditDiscoverResponse(
        items=[],
        meta=RedditDiscoverMeta(
            auto_mode=True,
            queued_count=queued,
            skipped_count=result["skipped"],
            errors=result["errors"],
        ),
    )


# ============ 账号矩阵档案 ============
@router.get("/accounts/profiles", response_model=list[RedditAccountOut])
def list_account_profiles(
    db: Session = Depends(get_db),
    ctx: SiteContext = Depends(get_site_context),
) -> list[RedditAccountOut]:
    accounts = reddit_oauth.list_reddit_accounts(db, ctx.site.id)
    for acc in accounts:
        get_or_create_profile(db, ctx.site.id, acc.id)
    return _accounts_out(db, accounts)


@router.patch("/accounts/{account_id}/profile", response_model=RedditAccountOut)
def update_account_profile(
    account_id: int,
    payload: RedditAccountProfileUpdate,
    db: Session = Depends(get_db),
    ctx: SiteContext = Depends(require_site_write),
) -> RedditAccountOut:
    account = reddit_oauth.get_site_account(db, ctx.site.id, account_id)
    if not account:
        raise HTTPException(status_code=404, detail="Reddit 账号不存在")
    profile = get_or_create_profile(db, ctx.site.id, account_id)
    data = payload.model_dump(exclude_unset=True)
    if data.get("apply_role_defaults"):
        if data.get("role"):
            profile.role = data["role"]
        apply_role_defaults(profile)
        data = {k: v for k, v in data.items() if k not in ("role", "apply_role_defaults")}
    if payload.clear_warning:
        clear_account_warning(db, profile)
    data.pop("clear_warning", None)
    data.pop("apply_role_defaults", None)
    for k, v in data.items():
        if v is None:
            continue
        if k == "persona_config":
            profile.persona_config = v
            voice = v.get("voice") if isinstance(v, dict) else None
            if voice:
                profile.persona = str(voice)[:200]
            continue
        setattr(profile, k, v)
    ensure_karma_stage_consistency(db, profile)
    db.commit()
    db.refresh(profile)
    return _accounts_out(db, [account])[0]


# ============ 社区库 ============
def _community_name_taken(
    db: Session, site_id: int, name: str, purpose: str, account_id: int | None
) -> RedditCommunity | None:
    q = db.query(RedditCommunity).filter(
        RedditCommunity.site_id == site_id,
        RedditCommunity.name == name,
        RedditCommunity.purpose == purpose,
    )
    if purpose == "persona":
        q = q.filter(RedditCommunity.account_id == account_id)
    else:
        q = q.filter(RedditCommunity.account_id.is_(None))
    return q.first()


@router.get("/communities", response_model=list[RedditCommunityOut])
def list_communities(
    account_id: int | None = None,
    db: Session = Depends(get_db),
    ctx: SiteContext = Depends(get_site_context),
) -> list[RedditCommunity]:
    q = db.query(RedditCommunity).filter(RedditCommunity.site_id == ctx.site.id)
    if account_id:
        q = q.filter(
            or_(
                (RedditCommunity.purpose == "promo") & RedditCommunity.account_id.is_(None),
                (RedditCommunity.purpose == "persona") & (RedditCommunity.account_id == account_id),
            )
        )
    return q.order_by(RedditCommunity.priority.asc(), RedditCommunity.name.asc()).all()


@router.post("/communities", response_model=RedditCommunityOut, status_code=status.HTTP_201_CREATED)
def create_community(
    payload: RedditCommunityCreate,
    db: Session = Depends(get_db),
    ctx: SiteContext = Depends(require_site_write),
) -> RedditCommunity:
    name = normalize_subreddit(payload.name)
    owner = payload.account_id if payload.purpose == "persona" else None
    if payload.purpose == "persona" and not owner:
        raise HTTPException(status_code=400, detail="人设社区必须指定所属账号")
    if _community_name_taken(db, ctx.site.id, name, payload.purpose, owner):
        raise HTTPException(status_code=409, detail=f"r/{name} 已在该账号的社区库中")
    verified = None
    if owner:
        account = reddit_oauth.get_site_account(db, ctx.site.id, owner)
        if account:
            ai = None
            try:
                ai = get_ai_client()
            except DeepSeekError:
                ai = None
            interests: list[str] = []
            profile = get_or_create_profile(db, ctx.site.id, owner)
            interests = parse_persona(profile.persona, config=profile.persona_config).interests
            client = get_reddit_client_for_account(account)
            judged = verify_subreddits_with_feed_llm(
                [name],
                interests=interests or [name],
                client=client,
                ai=ai,
            )
            verified = judged[0] if judged else None
    if verified is None:
        verified = verify_subreddit(name)
    if verified.exists is False:
        raise HTTPException(status_code=400, detail=f"r/{name} 不存在或无法访问（{verified.error}）")
    data = payload.model_dump(exclude_unset=True)
    data.pop("account_id", None)
    row = RedditCommunity(site_id=ctx.site.id, account_id=owner, **data)
    row.name = name
    apply_verify_to_community(row, verified)
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


@router.patch("/communities/{community_id}", response_model=RedditCommunityOut)
def update_community(
    community_id: int,
    payload: RedditCommunityUpdate,
    db: Session = Depends(get_db),
    ctx: SiteContext = Depends(require_site_write),
) -> RedditCommunity:
    row = (
        db.query(RedditCommunity)
        .filter(RedditCommunity.id == community_id, RedditCommunity.site_id == ctx.site.id)
        .first()
    )
    if not row:
        raise HTTPException(status_code=404, detail="社区不存在")
    data = payload.model_dump(exclude_unset=True)
    if "name" in data and data["name"]:
        data["name"] = normalize_subreddit(data["name"])
    for k, v in data.items():
        setattr(row, k, v)
    db.commit()
    db.refresh(row)
    return row


@router.delete("/communities/{community_id}", status_code=status.HTTP_204_NO_CONTENT, response_class=Response)
def delete_community(
    community_id: int,
    db: Session = Depends(get_db),
    ctx: SiteContext = Depends(require_site_write),
):
    row = (
        db.query(RedditCommunity)
        .filter(RedditCommunity.id == community_id, RedditCommunity.site_id == ctx.site.id)
        .first()
    )
    if not row:
        raise HTTPException(status_code=404, detail="社区不存在")
    db.delete(row)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/communities/suggest", response_model=RedditCommunitySuggestOut)
def suggest_communities(
    payload: RedditCommunitySuggestIn,
    db: Session = Depends(get_db),
    ctx: SiteContext = Depends(require_site_write),
) -> RedditCommunitySuggestOut:
    if not payload.account_id:
        raise HTTPException(status_code=400, detail="请先选择 Reddit 账号")
    account = reddit_oauth.get_site_account(db, ctx.site.id, payload.account_id)
    if not account:
        raise HTTPException(status_code=404, detail="Reddit 账号不存在")
    interests = [i.strip() for i in payload.interests if i and i.strip()]
    if not interests:
        profile = get_or_create_profile(db, ctx.site.id, payload.account_id)
        interests = parse_persona(profile.persona, config=profile.persona_config).interests
    if not interests:
        raise HTTPException(status_code=400, detail="请先填写人设兴趣，或在请求里传入 interests")
    ai = None
    try:
        ai = get_ai_client()
    except DeepSeekError:
        ai = None
    suggested = suggest_persona_subreddits(interests, ai=ai)
    existing = {
        c.name.lower()
        for c in db.query(RedditCommunity)
        .filter(
            RedditCommunity.site_id == ctx.site.id,
            RedditCommunity.purpose == "persona",
            RedditCommunity.account_id == payload.account_id,
        )
        .all()
    }
    to_check = [name for name in suggested if name.lower() not in existing]
    kept: list[str] = [name for name in suggested if name.lower() in existing]
    added = 0
    if to_check:
        client = get_reddit_client_for_account(account)
        judged = verify_subreddits_with_feed_llm(
            to_check,
            interests=interests,
            client=client,
            ai=ai,
        )
        for result in judged:
            if result.exists is False or not result.is_active_enough:
                continue
            row = RedditCommunity(
                site_id=ctx.site.id,
                account_id=payload.account_id,
                name=result.name,
                category="longtail",
                purpose="persona",
            )
            apply_verify_to_community(row, result)
            db.add(row)
            existing.add(result.name.lower())
            kept.append(result.name)
            added += 1
    db.commit()
    return RedditCommunitySuggestOut(suggested=kept, added=added)


@router.get("/brands", response_model=list[RedditBrandOut])
def list_brands(
    db: Session = Depends(get_db),
    ctx: SiteContext = Depends(get_site_context),
) -> list[RedditBrandOut]:
    rows = (
        db.query(RedditBrand)
        .filter(RedditBrand.site_id == ctx.site.id)
        .order_by(RedditBrand.id.asc())
        .all()
    )
    return [_brand_out(b) for b in rows]


@router.post("/brands", response_model=RedditBrandOut, status_code=status.HTTP_201_CREATED)
def create_brand(
    payload: RedditBrandIn,
    db: Session = Depends(get_db),
    ctx: SiteContext = Depends(require_site_write),
) -> RedditBrandOut:
    name = payload.name.strip()
    exists = (
        db.query(RedditBrand)
        .filter(RedditBrand.site_id == ctx.site.id, RedditBrand.name == name)
        .first()
    )
    if exists:
        raise HTTPException(status_code=409, detail=f"品牌「{name}」已存在")
    row = RedditBrand(site_id=ctx.site.id, name=name, is_active=payload.is_active)
    db.add(row)
    db.commit()
    db.refresh(row)
    return _brand_out(row)


@router.patch("/brands/{brand_id}", response_model=RedditBrandOut)
def update_brand(
    brand_id: int,
    payload: RedditBrandUpdate,
    db: Session = Depends(get_db),
    ctx: SiteContext = Depends(require_site_write),
) -> RedditBrandOut:
    row = (
        db.query(RedditBrand)
        .filter(RedditBrand.id == brand_id, RedditBrand.site_id == ctx.site.id)
        .first()
    )
    if not row:
        raise HTTPException(status_code=404, detail="品牌不存在")
    data = payload.model_dump(exclude_unset=True)
    if "name" in data and data["name"]:
        data["name"] = data["name"].strip()
    for k, v in data.items():
        setattr(row, k, v)
    db.commit()
    db.refresh(row)
    return _brand_out(row)


@router.delete("/brands/{brand_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_brand(
    brand_id: int,
    db: Session = Depends(get_db),
    ctx: SiteContext = Depends(require_site_write),
):
    row = (
        db.query(RedditBrand)
        .filter(RedditBrand.id == brand_id, RedditBrand.site_id == ctx.site.id)
        .first()
    )
    if not row:
        raise HTTPException(status_code=404, detail="品牌不存在")
    db.query(RedditComment).filter(RedditComment.brand_id == brand_id).update(
        {"brand_id": None, "product_id": None}, synchronize_session=False
    )
    db.delete(row)
    db.commit()


@router.post(
    "/brands/{brand_id}/products",
    response_model=RedditProductOut,
    status_code=status.HTTP_201_CREATED,
)
def create_product(
    brand_id: int,
    payload: RedditProductIn,
    db: Session = Depends(get_db),
    ctx: SiteContext = Depends(require_site_write),
) -> RedditProductOut:
    brand = (
        db.query(RedditBrand)
        .filter(RedditBrand.id == brand_id, RedditBrand.site_id == ctx.site.id)
        .first()
    )
    if not brand:
        raise HTTPException(status_code=404, detail="品牌不存在")
    name = payload.name.strip()
    row = RedditProduct(
        brand_id=brand.id,
        name=name,
        category=(payload.category or "").strip(),
        talking_points=[p.strip() for p in payload.talking_points if p.strip()][:8],
        is_active=payload.is_active,
    )
    db.add(row)
    db.flush()
    _set_product_communities(db, site_id=ctx.site.id, product=row, community_ids=payload.community_ids or [])
    _set_product_keywords(row, payload.keywords)
    db.commit()
    db.refresh(row)
    return _product_out(row)


@router.patch("/products/{product_id}", response_model=RedditProductOut)
def update_product(
    product_id: int,
    payload: RedditProductUpdate,
    db: Session = Depends(get_db),
    ctx: SiteContext = Depends(require_site_write),
) -> RedditProductOut:
    row = (
        db.query(RedditProduct)
        .join(RedditBrand, RedditBrand.id == RedditProduct.brand_id)
        .filter(RedditProduct.id == product_id, RedditBrand.site_id == ctx.site.id)
        .first()
    )
    if not row:
        raise HTTPException(status_code=404, detail="产品不存在")
    data = payload.model_dump(exclude_unset=True)
    community_ids = data.pop("community_ids", None)
    keywords = data.pop("keywords", None)
    if "name" in data and data["name"]:
        data["name"] = data["name"].strip()
    if "category" in data and data["category"] is not None:
        data["category"] = data["category"].strip()
    if "talking_points" in data and data["talking_points"] is not None:
        data["talking_points"] = [p.strip() for p in data["talking_points"] if p.strip()][:8]
    for k, v in data.items():
        setattr(row, k, v)
    if community_ids is not None:
        _set_product_communities(db, site_id=ctx.site.id, product=row, community_ids=community_ids)
    if keywords is not None:
        _set_product_keywords(row, keywords)
    db.commit()
    db.refresh(row)
    return _product_out(row)


@router.delete("/products/{product_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_product(
    product_id: int,
    db: Session = Depends(get_db),
    ctx: SiteContext = Depends(require_site_write),
):
    row = (
        db.query(RedditProduct)
        .join(RedditBrand, RedditBrand.id == RedditProduct.brand_id)
        .filter(RedditProduct.id == product_id, RedditBrand.site_id == ctx.site.id)
        .first()
    )
    if not row:
        raise HTTPException(status_code=404, detail="产品不存在")
    db.query(RedditComment).filter(RedditComment.product_id == product_id).update(
        {"product_id": None}, synchronize_session=False
    )
    db.delete(row)
    db.commit()


# ============ 帖子数据指标 ============
@router.get("/posts/{post_id}/metrics", response_model=list[RedditMetricOut])
def list_post_metrics(
    post_id: int,
    db: Session = Depends(get_db),
    ctx: SiteContext = Depends(get_site_context),
) -> list[RedditPostMetric]:
    post = db.query(RedditPost).filter(RedditPost.id == post_id, RedditPost.site_id == ctx.site.id).first()
    if not post:
        raise HTTPException(status_code=404, detail="发帖任务不存在")
    return (
        db.query(RedditPostMetric)
        .filter(RedditPostMetric.post_id == post_id)
        .order_by(RedditPostMetric.synced_at.asc())
        .all()
    )


@router.post("/posts/{post_id}/metrics/sync", response_model=list[RedditMetricOut])
def sync_post_metrics(
    post_id: int,
    db: Session = Depends(get_db),
    ctx: SiteContext = Depends(require_site_write),
) -> list[RedditPostMetric]:
    """立即同步单条已发布帖子的互动数据。"""
    post = db.query(RedditPost).filter(RedditPost.id == post_id, RedditPost.site_id == ctx.site.id).first()
    if not post:
        raise HTTPException(status_code=404, detail="发帖任务不存在")
    if post.status != "posted" or not post.reddit_post_id:
        raise HTTPException(status_code=400, detail="仅已发布的帖子支持同步数据")
    client = get_reddit_client_for_account(post.account)
    try:
        items = client.search_posts(post.subreddit, post.title[:80], limit=10)
    except RedditApiError as exc:
        _raise_reddit_upstream(exc)
        return []
    short_id = post.reddit_post_id.removeprefix("t3_")
    match = next(
        (
            it
            for it in items
            if str(it.get("thing_id") or "") == post.reddit_post_id
            or str(it.get("thing_id") or "").endswith(short_id)
        ),
        None,
    )
    if match:
        db.add(
            RedditPostMetric(
                post_id=post.id,
                score=int(match.get("score", 0)),
                num_comments=int(match.get("num_comments", 0)),
                synced_at=datetime.utcnow(),
            )
        )
        db.commit()
    return (
        db.query(RedditPostMetric)
        .filter(RedditPostMetric.post_id == post_id)
        .order_by(RedditPostMetric.synced_at.asc())
        .all()
    )


# ============ 运营概览（量化考核指标） ============
@router.get("/overview", response_model=RedditOverviewOut)
def reddit_overview(
    db: Session = Depends(get_db),
    ctx: SiteContext = Depends(get_site_context),
) -> RedditOverviewOut:
    day_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    week_ago = datetime.utcnow() - timedelta(days=7)

    def _count(model, *conditions):
        return db.query(model).filter(*conditions).count()

    posts_posted_today = _count(
        RedditPost,
        RedditPost.site_id == ctx.site.id,
        RedditPost.status == "posted",
        RedditPost.published_at >= day_start,
    )
    comments_posted_today = _count(
        RedditComment,
        RedditComment.site_id == ctx.site.id,
        RedditComment.status == "posted",
        RedditComment.published_at >= day_start,
    )
    recent_post_ids = [
        row[0]
        for row in db.query(RedditPost.id)
        .filter(
            RedditPost.site_id == ctx.site.id,
            RedditPost.status == "posted",
            RedditPost.published_at >= week_ago,
        )
        .all()
    ]
    recent_avg: float | None = None
    recent_total_score = 0
    recent_total_comments = 0
    if recent_post_ids:
        metrics = (
            db.query(RedditPostMetric)
            .filter(RedditPostMetric.post_id.in_(recent_post_ids))
            .order_by(RedditPostMetric.synced_at.asc())
            .all()
        )
        latest: dict[int, RedditPostMetric] = {}
        for m in metrics:
            latest[m.post_id] = m
        if latest:
            recent_avg = sum(m.score for m in latest.values()) / len(latest)
            recent_total_score = sum(m.score for m in latest.values())
            recent_total_comments = sum(m.num_comments for m in latest.values())

    warning_accounts = _count(
        RedditAccountProfile,
        RedditAccountProfile.site_id == ctx.site.id,
        RedditAccountProfile.risk_status == "warning",
    )
    mix = count_mix_window(db, ctx.site.id)

    return RedditOverviewOut(
        posts_pending=_count(RedditPost, RedditPost.site_id == ctx.site.id, RedditPost.status == "pending_review"),
        posts_approved=_count(RedditPost, RedditPost.site_id == ctx.site.id, RedditPost.status == "approved"),
        posts_posted=_count(RedditPost, RedditPost.site_id == ctx.site.id, RedditPost.status == "posted"),
        posts_posted_today=posts_posted_today,
        posts_scheduled=_count(
            RedditPost,
            RedditPost.site_id == ctx.site.id,
            RedditPost.status == "approved",
            RedditPost.scheduled_at.isnot(None),
        ),
        comments_pending=_count(RedditComment, RedditComment.site_id == ctx.site.id, RedditComment.status == "pending_review"),
        comments_posted_today=comments_posted_today,
        accounts_warning=warning_accounts,
        recent_avg_score=round(recent_avg, 2) if recent_avg is not None else None,
        recent_total_score=recent_total_score,
        recent_total_comments=recent_total_comments,
        promo_ratio_7d=round(mix.promo_ratio, 3),
        promo_count_7d=mix.promo,
        casual_count_7d=mix.casual,
        persona_communities=_count(
            RedditCommunity,
            RedditCommunity.site_id == ctx.site.id,
            RedditCommunity.purpose == "persona",
            RedditCommunity.is_active.is_(True),
        ),
        promo_communities=_count(
            RedditCommunity,
            RedditCommunity.site_id == ctx.site.id,
            RedditCommunity.purpose == "promo",
            RedditCommunity.is_active.is_(True),
        ),
        comments_likely_ai=_count(
            RedditComment,
            RedditComment.site_id == ctx.site.id,
            RedditComment.ai_risk == "likely_ai",
        ),
    )
