from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

import logging

from app.core.timeutil import as_bj_naive, now_bj

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from fastapi.responses import RedirectResponse
from sqlalchemy import func, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from app.api.deps import SiteContext, get_current_user, get_site_context, require_site_write
from app.core.database import get_db
from app.models.reddit import (
    RedditAccountProfile,
    RedditBrand,
    RedditCampaign,
    RedditCampaignAction,
    RedditComment,
    RedditCommunity,
    RedditPost,
    RedditPostMetric,
    RedditProduct,
    RedditProductKeyword,
    RedditVoteLog,
)
from app.models.social import SocialAccount
from app.models.user import User
from app.models.zernio_key import ZernioApiKey
from app.schemas.reddit import (
    RedditAccountProfileUpdate,
    RedditBatchRetryIn,
    RedditBatchRetryItem,
    RedditBatchRetryOut,
    RedditBrandIn,
    RedditBrandOut,
    RedditBrandUpdate,
    RedditCampaignActionOut,
    RedditCampaignCreate,
    RedditCampaignOut,
    RedditCampaignReportOut,
    RedditCampaignUpdate,
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
    apply_rules_to_community,
    apply_verify_to_community,
    fetch_subreddit_rules,
    fetch_subreddit_rules_via_zernio,
    refresh_community_existence_and_rules,
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
from app.tasks.reddit_campaign_tasks import trigger_campaign_tick
from app.tasks.reddit_tasks import (
    publish_reddit_comment_by_id,
    publish_reddit_post_by_id,
    try_enqueue_publish,
)
from app.services.reddit_publish import (
    force_fail_comment,
    force_fail_post,
    publish_comment_now,
    publish_post_now,
)
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

logger = logging.getLogger(__name__)

_EDITABLE_STATUSES = {"draft", "pending_review", "approved", "failed"}


def _risk_http(exc: RiskViolation) -> HTTPException:
    """风控拦截 → 409，返回阻断与提示明细。"""
    detail: dict = {"message": "发布被风控规则拦截", "errors": exc.errors, "warnings": exc.warnings}
    return HTTPException(status_code=409, detail=detail)


def _mix_http(exc: MixQuotaExceeded) -> HTTPException:
    return HTTPException(status_code=409, detail=str(exc))


def _purpose_for_subreddit(db: Session, site_id: int, subreddit: str) -> str | None:
    row = _community_for_subreddit(db, site_id, subreddit)
    return row.purpose if row else None


def _community_for_subreddit(
    db: Session,
    site_id: int,
    subreddit: str,
    *,
    account_id: int | None = None,
) -> RedditCommunity | None:
    name = normalize_subreddit(subreddit)
    q = db.query(RedditCommunity).filter(
        RedditCommunity.site_id == site_id,
        RedditCommunity.name == name,
    )
    rows = q.all()
    if not rows:
        return None
    if account_id is not None:
        for row in rows:
            if row.purpose == "persona" and row.account_id == account_id:
                return row
        for row in rows:
            if row.purpose == "promo" and row.account_id is None:
                return row
    # Prefer a row that already has official rules
    for row in rows:
        if row.rules_text:
            return row
    return rows[0]


def _rules_text_for_subreddit(
    db: Session,
    site_id: int,
    subreddit: str,
    *,
    account_id: int | None = None,
) -> str | None:
    row = _community_for_subreddit(db, site_id, subreddit, account_id=account_id)
    return (row.rules_text or None) if row else None


def _reddit_client_for_rules(
    db: Session,
    site_id: int,
    *,
    preferred_account_id: int | None = None,
):
    """选一个已同步的 Zernio Reddit 账号用于拉版规。"""
    if preferred_account_id:
        acc = reddit_oauth.get_site_account(db, site_id, preferred_account_id)
        if acc:
            return get_reddit_client_for_account(acc)
    accounts = reddit_oauth.list_reddit_accounts(db, site_id)
    for acc in accounts:
        if getattr(acc, "is_active", True):
            return get_reddit_client_for_account(acc)
    if accounts:
        return get_reddit_client_for_account(accounts[0])
    return None


def _fetch_community_rules(
    db: Session,
    site_id: int,
    name: str,
    *,
    preferred_account_id: int | None = None,
    client=None,
):
    """优先 Zernio；无账号时再试公开/OAuth 回退。"""
    zc = client or _reddit_client_for_rules(
        db, site_id, preferred_account_id=preferred_account_id
    )
    if zc is not None:
        return fetch_subreddit_rules_via_zernio(zc, name)
    return fetch_subreddit_rules(name)


def _persona_prompt_for(
    db: Session,
    site_id: int,
    account_id: int,
    *,
    subreddit: str | None = None,
    seed: int | None = None,
) -> str:
    profile = (
        db.query(RedditAccountProfile)
        .filter(RedditAccountProfile.site_id == site_id, RedditAccountProfile.account_id == account_id)
        .first()
    )
    if not profile:
        return ""
    persona = parse_persona(profile.persona, config=profile.persona_config)
    return persona.to_prompt(subreddit=subreddit, seed=seed)


def _brief_from_product(brand: RedditBrand, product: RedditProduct) -> dict:
    return {
        "brand": brand.name or "",
        "product": product.name or "",
        "category": product.category or "",
        "description": (product.description or "").strip(),
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
        raise HTTPException(status_code=400, detail="请选择品牌和产品")
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


def _fill_post_out(post: RedditPost, db: Session | None = None) -> RedditPost:
    if post.account:
        post.account_name = reddit_oauth.display_reddit_name(post.account.account_name)
    post.planned_at = post.scheduled_at
    if db is not None and post.campaign_id:
        campaign = db.get(RedditCampaign, post.campaign_id)
        post.campaign_name = campaign.name if campaign else None
        if post.scheduled_at is None:
            # 待审内容尚无定时：展示关联动作的计划时间，供待审队列提示
            action = (
                db.query(RedditCampaignAction)
                .filter(
                    RedditCampaignAction.post_id == post.id,
                    RedditCampaignAction.action_type == "post",
                )
                .order_by(RedditCampaignAction.id.desc())
                .first()
            )
            if action is not None:
                post.planned_at = action.planned_at
    else:
        post.campaign_name = None
    return post


def _queue_numbers(db: Session, model, site_id: int, account_ids: set[int]) -> dict[int, int]:
    """审核队列展示用序号：每个账号内按创建时间升序从 1 编号（帖/评论各自独立）。"""
    ids = [a for a in account_ids if a]
    if not ids:
        return {}
    rn = func.row_number().over(
        partition_by=model.account_id,
        order_by=(model.created_at.asc(), model.id.asc()),
    )
    rows = (
        db.query(model.id, rn.label("seq"))
        .filter(model.site_id == site_id, model.account_id.in_(ids))
        .all()
    )
    return {int(row[0]): int(row[1]) for row in rows}


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
    comment.planned_at = comment.scheduled_at
    if comment.campaign_id:
        campaign = db.get(RedditCampaign, comment.campaign_id)
        comment.campaign_name = campaign.name if campaign else None
        if comment.scheduled_at is None:
            # 待审内容尚无定时：展示关联动作的计划时间，供待审队列提示
            action = (
                db.query(RedditCampaignAction)
                .filter(
                    RedditCampaignAction.comment_id == comment.id,
                    RedditCampaignAction.action_type == "comment",
                )
                .order_by(RedditCampaignAction.id.desc())
                .first()
            )
            if action is not None:
                comment.planned_at = action.planned_at
    else:
        comment.campaign_name = None
    return comment


def _fill_posts_out(db: Session, posts: list[RedditPost]) -> list[RedditPost]:
    """列表批量填充（campaign 名/动作计划时间），消除逐条查询的 N+1。

    account 关系由调用方用 joinedload 预载；此处只做 campaign/action 两次 IN 查询。
    """
    campaign_ids = {p.campaign_id for p in posts if p.campaign_id}
    campaigns = (
        {
            c.id: c
            for c in db.query(RedditCampaign.id, RedditCampaign.name)
            .filter(RedditCampaign.id.in_(campaign_ids))
            .all()
        }
        if campaign_ids
        else {}
    )
    need_action = [p.id for p in posts if p.campaign_id and p.scheduled_at is None]
    latest_planned: dict[int, object] = {}
    if need_action:
        for pid, planned in (
            db.query(RedditCampaignAction.post_id, RedditCampaignAction.planned_at)
            .filter(
                RedditCampaignAction.post_id.in_(need_action),
                RedditCampaignAction.action_type == "post",
            )
            .order_by(RedditCampaignAction.id.asc())
            .all()
        ):
            latest_planned[pid] = planned  # id 升序后写覆盖 = 最新动作
    for p in posts:
        if p.account:
            p.account_name = reddit_oauth.display_reddit_name(p.account.account_name)
        p.planned_at = p.scheduled_at
        if p.campaign_id:
            campaign = campaigns.get(p.campaign_id)
            p.campaign_name = campaign.name if campaign else None
            if p.scheduled_at is None and p.id in latest_planned:
                p.planned_at = latest_planned[p.id]
        else:
            p.campaign_name = None
    return posts


def _fill_comments_out(db: Session, comments: list[RedditComment]) -> list[RedditComment]:
    """评论列表批量填充（brand/product/campaign/动作计划时间），消除 N+1。"""
    brand_ids = {c.brand_id for c in comments if c.brand_id}
    product_ids = {c.product_id for c in comments if c.product_id}
    campaign_ids = {c.campaign_id for c in comments if c.campaign_id}
    brands = (
        {
            b.id: b.name
            for b in db.query(RedditBrand.id, RedditBrand.name)
            .filter(RedditBrand.id.in_(brand_ids))
            .all()
        }
        if brand_ids
        else {}
    )
    products = (
        {
            p.id: p.name
            for p in db.query(RedditProduct.id, RedditProduct.name)
            .filter(RedditProduct.id.in_(product_ids))
            .all()
        }
        if product_ids
        else {}
    )
    campaigns = (
        {
            c.id: c.name
            for c in db.query(RedditCampaign.id, RedditCampaign.name)
            .filter(RedditCampaign.id.in_(campaign_ids))
            .all()
        }
        if campaign_ids
        else {}
    )
    need_action = [c.id for c in comments if c.campaign_id and c.scheduled_at is None]
    latest_planned: dict[int, object] = {}
    if need_action:
        for cid, planned in (
            db.query(RedditCampaignAction.comment_id, RedditCampaignAction.planned_at)
            .filter(
                RedditCampaignAction.comment_id.in_(need_action),
                RedditCampaignAction.action_type == "comment",
            )
            .order_by(RedditCampaignAction.id.asc())
            .all()
        ):
            latest_planned[cid] = planned
    for c in comments:
        if c.account:
            c.account_name = reddit_oauth.display_reddit_name(c.account.account_name)
        c.brand_name = brands.get(c.brand_id) if c.brand_id else None
        c.product_name = products.get(c.product_id) if c.product_id else None
        c.planned_at = c.scheduled_at
        if c.campaign_id:
            c.campaign_name = campaigns.get(c.campaign_id)
            if c.scheduled_at is None and c.id in latest_planned:
                c.planned_at = latest_planned[c.id]
        else:
            c.campaign_name = None
    return comments


def _product_out(product: RedditProduct) -> RedditProductOut:
    communities = sorted(product.communities or [], key=lambda c: c.id)
    keywords = [r.keyword for r in (product.keyword_rows or [])]
    brand_name = ""
    if getattr(product, "brand", None) is not None:
        brand_name = product.brand.name or ""
    return RedditProductOut(
        id=product.id,
        brand_id=product.brand_id,
        brand_name=brand_name,
        name=product.name,
        category=product.category or "",
        description=product.description or "",
        talking_points=product.talking_points or [],
        keywords=keywords,
        is_active=product.is_active,
        community_ids=[c.id for c in communities],
        community_names=[c.name for c in communities],
        created_at=product.created_at,
        updated_at=product.updated_at,
    )


def _set_product_keywords(
    db: Session,
    product: RedditProduct,
    keywords: list[str] | None,
) -> None:
    from app.services.reddit_discover import normalize_product_keywords

    cleaned = normalize_product_keywords(keywords)
    # 先删后插，避免同 (product_id, keyword) 唯一约束在「先插后删」时冲突
    product.keyword_rows.clear()
    db.flush()
    product.keyword_rows = [RedditProductKeyword(keyword=kw) for kw in cleaned]


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


def _accounts_out(db: Session, accounts, site_id: int) -> list[RedditAccountOut]:
    labels = {k.id: k.label for k in list_all_keys(db, site_id)}
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
    site_id: int,
    accounts,
    sync_errors: list[str] | None = None,
) -> RedditStatusOut:
    return RedditStatusOut(
        configured=is_zernio_ready(db, site_id),
        connected=bool(accounts),
        accounts=_accounts_out(db, accounts, site_id),
        zernio_key_count=len(list_enabled_keys(db, site_id)),
        sync_errors=sync_errors or [],
    )


@router.get("/status", response_model=RedditStatusOut)
def reddit_status(
    db: Session = Depends(get_db),
    ctx: SiteContext = Depends(get_site_context),
) -> RedditStatusOut:
    return _status_out(db, ctx.site.id, reddit_oauth.list_reddit_accounts(db, ctx.site.id))


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
    return _status_out(db, ctx.site.id, accounts, errors)


@router.get("/zernio-keys", response_model=list[ZernioKeyOut])
def list_zernio_keys(
    db: Session = Depends(get_db),
    ctx: SiteContext = Depends(get_site_context),
) -> list[ZernioKeyOut]:
    return [_key_out(row) for row in list_all_keys(db, ctx.site.id)]


@router.post("/zernio-keys", response_model=ZernioKeyOut, status_code=status.HTTP_201_CREATED)
def create_zernio_key(
    payload: ZernioKeyCreate,
    db: Session = Depends(get_db),
    ctx: SiteContext = Depends(require_site_write),
) -> ZernioKeyOut:
    row = ZernioApiKey(
        site_id=ctx.site.id,
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
    row = (
        db.query(ZernioApiKey)
        .filter(ZernioApiKey.id == key_id, ZernioApiKey.site_id == ctx.site.id)
        .first()
    )
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
    row = (
        db.query(ZernioApiKey)
        .filter(ZernioApiKey.id == key_id, ZernioApiKey.site_id == ctx.site.id)
        .first()
    )
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

    allow_product = bool(payload.allow_product)
    if allow_product and not payload.product_id:
        raise HTTPException(status_code=400, detail="开启「允许提及产品」时必须选择产品")

    from app.services.reddit_content import generate_post_content

    try:
        generated = generate_post_content(
            db,
            site=ctx.site,
            account=account,
            subreddit=payload.subreddit,
            post_type=payload.post_type,
            allow_product=allow_product,
            include_site_url=bool(payload.include_site_url),
            brand_id=payload.brand_id,
            product_id=payload.product_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except DeepSeekError as exc:
        raise HTTPException(status_code=502, detail=f"AI 服务不可用：{exc}") from exc

    post = RedditPost(
        site_id=ctx.site.id,
        account_id=account.id,
        post_type=payload.post_type,
        subreddit=generated["subreddit"],
        keyword=generated["keyword"],
        title=generated["title"],
        body=generated["body"],
        site_url=generated["site_url"],
        status="pending_review",
        content_intent=generated["intent"],
        narrative_angle=generated.get("angle"),
        ai_risk=generated.get("ai_risk"),
        ai_score=generated.get("ai_score"),
    )
    db.add(post)
    db.commit()
    db.refresh(post)
    from app.services.reddit_community_rotation import record_activity

    record_activity(
        db,
        site_id=ctx.site.id,
        account_id=account.id,
        subreddit=post.subreddit,
        angle=generated.get("angle"),
    )
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
    posts = (
        q.options(joinedload(RedditPost.account))
        .order_by(RedditPost.created_at.desc())
        .limit(100)
        .all()
    )
    seq = _queue_numbers(db, RedditPost, ctx.site.id, {p.account_id for p in posts})
    out = _fill_posts_out(db, posts)
    for p in out:
        p.queue_no = seq.get(p.id)
    return out


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
    if post.campaign_id and post.scheduled_at is None:
        # campaign 内容：批准即按动作计划时间自动定时，保留活跃窗口内的随机分布
        action = (
            db.query(RedditCampaignAction)
            .filter(RedditCampaignAction.post_id == post.id, RedditCampaignAction.action_type == "post")
            .first()
        )
        if action and action.planned_at:
            planned = as_bj_naive(action.planned_at)
            # 计划时间已过：立即发布兜底，避免 approved 且无定时内容被发布扫描遗漏
            post.scheduled_at = planned if planned > now_bj() else now_bj()
    db.commit()
    db.refresh(post)
    if post.campaign_id:
        # 事件加速：立即触发一轮 campaign tick，消除最长 15 分钟的拾取延迟
        trigger_campaign_tick()
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
    except RedditApiError as exc:
        _raise_reddit_upstream(exc)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _fill_post_out(post)


@router.post("/posts/{post_id}/force-fail", response_model=RedditPostOut)
def force_fail_post_endpoint(
    post_id: int,
    db: Session = Depends(get_db),
    ctx: SiteContext = Depends(require_site_write),
) -> RedditPost:
    """将卡住的 posting（无外部 ID）标为 failed，便于重试。"""
    post = db.query(RedditPost).filter(RedditPost.id == post_id, RedditPost.site_id == ctx.site.id).first()
    if not post:
        raise HTTPException(status_code=404, detail="发帖任务不存在")
    try:
        post = force_fail_post(db, post)
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
    scheduled = as_bj_naive(payload.scheduled_at)
    if scheduled <= now_bj():
        raise HTTPException(status_code=400, detail="定时时间必须晚于当前时间")
    post.scheduled_at = scheduled
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
    angle_tag: str | None = None,
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
    persona_prompt = _persona_prompt_for(
        db, ctx.site.id, account.id, subreddit=subreddit, seed=account.id + len(title)
    )
    product_brief = None
    resolved_brand_id = None
    resolved_product_id = None
    if intent == "promo":
        brand, product = _require_brand_product(db, ctx.site.id, brand_id, product_id)
        product_brief = _brief_from_product(brand, product)
        resolved_brand_id = brand.id
        resolved_product_id = product.id
    from app.services.reddit_community_rotation import (
        COMMENT_SIMILARITY_CORPUS_SIZE,
        COMMENT_SIMILARITY_REGEN_THRESHOLD,
        angle_hint as _angle_hint,
        max_ngram_similarity,
        pick_angle,
        recent_angles,
        record_activity,
    )

    if angle_tag is None:
        angle_tag = pick_angle(recent_angles(db, account.id, subreddit))
    angle_hint = _angle_hint(angle_tag)
    try:
        ai = get_ai_client()
        client = get_reddit_client_for_account(account)
        from app.services.reddit_style import fetch_community_examples

        community_examples = fetch_community_examples(client, subreddit)
        pipeline_kwargs = dict(
            post_title=title,
            post_body=body,
            subreddit=subreddit,
            intent=intent,
            persona_prompt=persona_prompt,
            product_brief=product_brief,
            site_url=site_url,
            community_rules=_rules_text_for_subreddit(
                db, ctx.site.id, subreddit, account_id=account.id
            ),
            seed=account.id + len(title),
            community_examples=community_examples,
            site_id=ctx.site.id,
        )
        generated = generate_comment_pipeline(ai, angle_hint=angle_hint, **pipeline_kwargs)
        comment_body = generated.body
        ai_risk = generated.ai_risk
        ai_score = generated.ai_score_after_humanize

        # 评论雷同检测：与本账号近 10 条已发布评论做 3-gram 相似度检查，
        # 超过阈值则换一个角度重生成一次，取相似度更低的版本（失败保留原稿）
        try:
            recent_bodies = [
                r[0]
                for r in db.query(RedditComment.body)
                .filter(
                    RedditComment.account_id == account.id,
                    RedditComment.status == "posted",
                )
                .order_by(RedditComment.id.desc())
                .limit(COMMENT_SIMILARITY_CORPUS_SIZE)
                .all()
            ]
            sim = max_ngram_similarity(comment_body, recent_bodies)
            if recent_bodies and sim > COMMENT_SIMILARITY_REGEN_THRESHOLD:
                alt_angle = pick_angle([angle_tag])
                alt = generate_comment_pipeline(
                    ai,
                    angle_hint=_angle_hint(alt_angle),
                    seed=account.id + len(title) + 17,
                    **pipeline_kwargs,
                )
                alt_sim = max_ngram_similarity(alt.body, recent_bodies)
                if alt_sim < sim:
                    comment_body = alt.body
                    ai_risk = alt.ai_risk
                    ai_score = alt.ai_score_after_humanize
                    angle_tag = alt_angle
                    logger.info(
                        "Comment regenerated for similarity: account=%s sub=%s sim=%.2f→%.2f",
                        account.id,
                        subreddit,
                        sim,
                        alt_sim,
                    )
        except DeepSeekError:
            logger.warning("Comment similarity regen failed; keeping original", exc_info=True)
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
        ai_score=ai_score,
        brand_id=resolved_brand_id,
        product_id=resolved_product_id,
        status="pending_review",
    )
    db.add(comment)
    db.commit()
    db.refresh(comment)
    record_activity(
        db,
        site_id=ctx.site.id,
        account_id=account.id,
        subreddit=comment.subreddit,
        angle=angle_tag,
    )
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
    comments = (
        q.options(joinedload(RedditComment.account))
        .order_by(RedditComment.created_at.desc())
        .limit(100)
        .all()
    )
    seq = _queue_numbers(db, RedditComment, ctx.site.id, {c.account_id for c in comments})
    out = _fill_comments_out(db, comments)
    for c in out:
        c.queue_no = seq.get(c.id)
    return out


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
    if comment.campaign_id and comment.scheduled_at is None:
        # campaign 内容：批准即按动作计划时间自动定时，保留活跃窗口内的随机分布
        action = (
            db.query(RedditCampaignAction)
            .filter(RedditCampaignAction.comment_id == comment.id, RedditCampaignAction.action_type == "comment")
            .first()
        )
        if action and action.planned_at:
            planned = as_bj_naive(action.planned_at)
            # 计划时间已过：立即发布兜底，避免 approved 且无定时内容被发布扫描遗漏
            comment.scheduled_at = planned if planned > now_bj() else now_bj()
    db.commit()
    db.refresh(comment)
    if comment.campaign_id:
        # 事件加速：立即触发一轮 campaign tick，消除最长 15 分钟的拾取延迟
        trigger_campaign_tick()
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
    except RedditApiError as exc:
        _raise_reddit_upstream(exc)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _fill_comment_out(db, comment)


@router.post("/comments/{comment_id}/force-fail", response_model=RedditCommentOut)
def force_fail_comment_endpoint(
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
        comment = force_fail_comment(db, comment)
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
    scheduled = as_bj_naive(payload.scheduled_at)
    if scheduled <= now_bj():
        raise HTTPException(status_code=400, detail="定时时间必须晚于当前时间")
    comment.scheduled_at = scheduled
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
        if is_zernio_ready(db, ctx.site.id):
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
    day_start = now_bj().replace(hour=0, minute=0, second=0, microsecond=0)
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
    return _accounts_out(db, accounts, ctx.site.id)


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
    return _accounts_out(db, [account], ctx.site.id)[0]


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
    rules_client = None
    if owner:
        acc = reddit_oauth.get_site_account(db, ctx.site.id, owner)
        if acc:
            rules_client = get_reddit_client_for_account(acc)
    rules = _fetch_community_rules(
        db,
        ctx.site.id,
        name,
        preferred_account_id=owner,
        client=rules_client,
    )
    apply_rules_to_community(row, rules)
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


@router.post("/communities/{community_id}/refresh-rules", response_model=RedditCommunityOut)
def refresh_community_rules(
    community_id: int,
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
    zc = _reddit_client_for_rules(
        db, ctx.site.id, preferred_account_id=row.account_id
    )
    if zc is None:
        raise HTTPException(
            status_code=400,
            detail="请先在「社交账号」同步至少一个 Reddit 账号（Zernio），再用它拉取版规",
        )
    # 只打 Zernio 版规接口，不再强制公开 about.json（易 403，且占用时机）
    verify, rules = refresh_community_existence_and_rules(
        row, force=True, zernio_client=zc, skip_public_verify=True
    )
    if verify.exists is False:
        db.commit()
        db.refresh(row)
        raise HTTPException(
            status_code=400,
            detail=f"r/{row.name} 不存在或无法访问（{verify.error}）",
        )
    if not rules.rules_text and rules.error:
        db.commit()
        db.refresh(row)
        # 限流且已有旧版规：保留旧数据，用 429 提示等待，避免误以为版规丢失
        if rules.rate_limited:
            headers = {}
            if rules.retry_after:
                headers["Retry-After"] = str(int(rules.retry_after))
            raise HTTPException(
                status_code=429,
                detail=rules.error
                + ("；已保留现有版规" if row.rules_text else ""),
                headers=headers or None,
            )
        raise HTTPException(
            status_code=502,
            detail=f"版规拉取失败：{rules.error}（社区仍保留，可稍后重试）",
        )
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
            rules = fetch_subreddit_rules_via_zernio(client, result.name)
            apply_rules_to_community(row, rules)
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


@router.get("/products", response_model=list[RedditProductOut])
def list_products(
    db: Session = Depends(get_db),
    ctx: SiteContext = Depends(get_site_context),
) -> list[RedditProductOut]:
    rows = (
        db.query(RedditProduct)
        .join(RedditBrand, RedditBrand.id == RedditProduct.brand_id)
        .filter(RedditBrand.site_id == ctx.site.id)
        .order_by(RedditBrand.id.asc(), RedditProduct.id.asc())
        .all()
    )
    return [_product_out(p) for p in rows]


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
        description=(payload.description or "").strip()[:2000] or None,
        talking_points=[p.strip() for p in payload.talking_points if p.strip()][:8],
        is_active=payload.is_active,
    )
    db.add(row)
    db.flush()
    _set_product_communities(db, site_id=ctx.site.id, product=row, community_ids=payload.community_ids or [])
    _set_product_keywords(db, row, payload.keywords)
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
    if "description" in data and data["description"] is not None:
        data["description"] = data["description"].strip()[:2000] or None
    if "talking_points" in data and data["talking_points"] is not None:
        data["talking_points"] = [p.strip() for p in data["talking_points"] if p.strip()][:8]
    for k, v in data.items():
        setattr(row, k, v)
    if community_ids is not None:
        _set_product_communities(db, site_id=ctx.site.id, product=row, community_ids=community_ids)
    if keywords is not None:
        _set_product_keywords(db, row, keywords)
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
                synced_at=now_bj(),
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
    day_start = now_bj().replace(hour=0, minute=0, second=0, microsecond=0)
    week_ago = now_bj() - timedelta(days=7)

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


# ============ 自动化 Campaign ============
def _campaign_or_404(db: Session, ctx: SiteContext, campaign_id: int) -> RedditCampaign:
    row = (
        db.query(RedditCampaign)
        .filter(RedditCampaign.id == campaign_id, RedditCampaign.site_id == ctx.site.id)
        .first()
    )
    if not row:
        raise HTTPException(status_code=404, detail="Campaign 不存在")
    return row


def _campaign_out(db: Session, campaign: RedditCampaign) -> RedditCampaignOut:
    from app.services.reddit_campaign import _quota_used

    out = RedditCampaignOut.model_validate(campaign)
    quota = _quota_used(db, campaign.id, now_bj())
    out.today_posts = quota["posts"]
    out.today_comments = quota["comments"]
    out.today_upvotes = quota["upvotes"]
    return out


def _normalize_campaign_subreddits(subreddits: list[str] | None) -> list[str]:
    from app.services.reddit_client import normalize_subreddit

    seen: list[str] = []
    for s in subreddits or []:
        name = normalize_subreddit(s)
        if name and name not in seen:
            seen.append(name)
    return seen


def _validate_campaign_accounts(db: Session, ctx: SiteContext, account_ids: list[int] | None) -> None:
    for aid in account_ids or []:
        try:
            reddit_oauth.get_site_account(db, ctx.site.id, aid)
        except HTTPException:
            raise HTTPException(status_code=400, detail=f"Reddit 账号 #{aid} 不存在或不属于本站")


def _validate_campaign_product(db: Session, ctx: SiteContext, product_id: int | None, brand_id: int | None) -> None:
    if product_id:
        product = db.get(RedditProduct, product_id)
        if not product:
            raise HTTPException(status_code=404, detail="产品不存在")
        brand = db.get(RedditBrand, product.brand_id)
        if not brand or brand.site_id != ctx.site.id:
            raise HTTPException(status_code=400, detail="产品不属于本站品牌")
    if brand_id:
        brand = db.get(RedditBrand, brand_id)
        if not brand or brand.site_id != ctx.site.id:
            raise HTTPException(status_code=400, detail="品牌不属于本站")


def _validate_campaign_dates(start, end) -> None:
    if end < start:
        raise HTTPException(status_code=400, detail="结束日期不能早于开始日期")


@router.get("/automation/campaigns", response_model=list[RedditCampaignOut])
def list_automation_campaigns(
    db: Session = Depends(get_db), ctx: SiteContext = Depends(get_site_context)
):
    rows = (
        db.query(RedditCampaign)
        .filter(RedditCampaign.site_id == ctx.site.id)
        .order_by(RedditCampaign.id.desc())
        .all()
    )
    return [_campaign_out(db, row) for row in rows]


@router.post("/automation/campaigns", response_model=RedditCampaignOut, status_code=201)
def create_automation_campaign(
    payload: RedditCampaignCreate,
    db: Session = Depends(get_db),
    ctx: SiteContext = Depends(require_site_write),
):
    _validate_campaign_dates(payload.start_date, payload.end_date)
    _validate_campaign_accounts(db, ctx, payload.account_ids)
    _validate_campaign_product(db, ctx, payload.product_id, payload.brand_id)
    data = payload.model_dump()
    data["subreddits"] = _normalize_campaign_subreddits(data.get("subreddits"))
    campaign = RedditCampaign(site_id=ctx.site.id, **data)
    db.add(campaign)
    db.commit()
    db.refresh(campaign)
    return _campaign_out(db, campaign)


@router.get("/automation/campaigns/{campaign_id}", response_model=RedditCampaignOut)
def get_automation_campaign(
    campaign_id: int,
    db: Session = Depends(get_db),
    ctx: SiteContext = Depends(get_site_context),
):
    return _campaign_out(db, _campaign_or_404(db, ctx, campaign_id))


@router.patch("/automation/campaigns/{campaign_id}", response_model=RedditCampaignOut)
def update_automation_campaign(
    campaign_id: int,
    payload: RedditCampaignUpdate,
    db: Session = Depends(get_db),
    ctx: SiteContext = Depends(require_site_write),
):
    campaign = _campaign_or_404(db, ctx, campaign_id)
    data = payload.model_dump(exclude_unset=True)
    if "subreddits" in data:
        data["subreddits"] = _normalize_campaign_subreddits(data["subreddits"])
    if "account_ids" in data:
        _validate_campaign_accounts(db, ctx, data["account_ids"])
    if "product_id" in data or "brand_id" in data:
        _validate_campaign_product(
            db,
            ctx,
            data.get("product_id", campaign.product_id),
            data.get("brand_id", campaign.brand_id),
        )
    for key, value in data.items():
        setattr(campaign, key, value)
    _validate_campaign_dates(campaign.start_date, campaign.end_date)
    db.commit()
    db.refresh(campaign)
    return _campaign_out(db, campaign)


@router.delete("/automation/campaigns/{campaign_id}", status_code=204)
def delete_automation_campaign(
    campaign_id: int,
    db: Session = Depends(get_db),
    ctx: SiteContext = Depends(require_site_write),
):
    campaign = _campaign_or_404(db, ctx, campaign_id)
    db.delete(campaign)
    db.commit()


@router.post("/automation/campaigns/{campaign_id}/start", response_model=RedditCampaignOut)
def start_automation_campaign(
    campaign_id: int,
    db: Session = Depends(get_db),
    ctx: SiteContext = Depends(require_site_write),
):
    campaign = _campaign_or_404(db, ctx, campaign_id)
    if campaign.status == "ended":
        raise HTTPException(status_code=400, detail="Campaign 已结束")
    if campaign.end_date < now_bj().date():
        raise HTTPException(status_code=400, detail="结束日期已过，请先延长日期区间")
    campaign.status = "running"
    db.commit()
    db.refresh(campaign)
    # 事件加速：启动即触发一轮 tick，立即完成首批动作排期
    trigger_campaign_tick()
    return _campaign_out(db, campaign)


@router.post("/automation/campaigns/{campaign_id}/pause", response_model=RedditCampaignOut)
def pause_automation_campaign(
    campaign_id: int,
    db: Session = Depends(get_db),
    ctx: SiteContext = Depends(require_site_write),
):
    campaign = _campaign_or_404(db, ctx, campaign_id)
    if campaign.status != "running":
        raise HTTPException(status_code=400, detail="仅运行中的 Campaign 可暂停")
    campaign.status = "paused"
    db.commit()
    db.refresh(campaign)
    return _campaign_out(db, campaign)


@router.get("/automation/campaigns/{campaign_id}/actions", response_model=list[RedditCampaignActionOut])
def list_campaign_actions(
    campaign_id: int,
    status: Optional[str] = None,
    action_type: Optional[str] = None,
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
    ctx: SiteContext = Depends(get_site_context),
):
    _campaign_or_404(db, ctx, campaign_id)
    q = db.query(RedditCampaignAction).filter(RedditCampaignAction.campaign_id == campaign_id)
    if status:
        q = q.filter(RedditCampaignAction.status == status)
    if action_type:
        q = q.filter(RedditCampaignAction.action_type == action_type)
    rows = q.order_by(RedditCampaignAction.id.desc()).limit(limit).all()
    names = {
        row[0]: row[1]
        for row in db.query(SocialAccount.id, SocialAccount.account_name)
        .filter(SocialAccount.site_id == ctx.site.id)
        .all()
    }
    out = []
    for row in rows:
        item = RedditCampaignActionOut.model_validate(row)
        item.account_name = names.get(row.account_id)
        out.append(item)
    return out


def _retry_one_action(db: Session, ctx: SiteContext, action_id: int) -> tuple[bool, str | None]:
    """重试单条失败动作（单条/批量共用）：返回 (ok, 失败原因)。

    点赞类回 pending + planned_at=now 并触发 tick；post/comment 类同步重置关联内容
    并立即入队发布，避免 reconcile 又把动作标回 failed。
    """
    action = db.get(RedditCampaignAction, action_id)
    if not action:
        return False, "动作记录不存在"
    campaign = (
        db.query(RedditCampaign)
        .filter(
            RedditCampaign.id == action.campaign_id,
            RedditCampaign.site_id == ctx.site.id,
        )
        .first()
    )
    if not campaign:
        return False, "动作记录不存在"
    if action.status != "failed":
        return False, "仅失败的动作可重试"

    now = now_bj()
    action.status = "pending"
    action.error_message = None

    if action.action_type in ("upvote_related", "upvote_self"):
        action.planned_at = now
        db.commit()
        db.refresh(action)
        trigger_campaign_tick()
        return True, None

    content = None
    kind = None
    if action.post_id:
        content = db.get(RedditPost, action.post_id)
        kind = "post"
    elif action.comment_id:
        content = db.get(RedditComment, action.comment_id)
        kind = "comment"
    if content is None:
        db.rollback()
        return False, "关联内容已删除，无法重试"
    content.status = "approved"
    content.scheduled_at = now
    content.error_message = None
    db.commit()
    db.refresh(action)
    try:
        task = publish_reddit_post_by_id if kind == "post" else publish_reddit_comment_by_id
        try_enqueue_publish(kind, content.id, task)
    except Exception:
        logger.warning("Retry enqueue publish failed for campaign action #%s", action_id, exc_info=True)
        # 入队失败不阻断：发布扫描任务会在 ≤15 分钟内拾取 approved + 到期的内容
    return True, None


@router.post("/automation/actions/{action_id}/retry", response_model=RedditCampaignActionOut)
def retry_campaign_action(
    action_id: int,
    db: Session = Depends(get_db),
    ctx: SiteContext = Depends(require_site_write),
):
    ok, detail = _retry_one_action(db, ctx, action_id)
    if not ok:
        status_code = 404 if detail == "动作记录不存在" else 400
        raise HTTPException(status_code=status_code, detail=detail)
    action = db.get(RedditCampaignAction, action_id)
    item = RedditCampaignActionOut.model_validate(action)
    account = db.get(SocialAccount, action.account_id)
    item.account_name = account.account_name if account else None
    return item


@router.post("/automation/actions/retry", response_model=RedditBatchRetryOut)
def retry_campaign_actions_batch(
    payload: RedditBatchRetryIn,
    db: Session = Depends(get_db),
    ctx: SiteContext = Depends(require_site_write),
):
    """批量重试失败动作：逐条独立执行，单条失败不影响其他。"""
    items: list[RedditBatchRetryItem] = []
    retried = 0
    for action_id in dict.fromkeys(payload.action_ids):  # 去重保序
        try:
            ok, detail = _retry_one_action(db, ctx, action_id)
        except Exception as exc:  # noqa: BLE001 — 单条异常不阻断批量
            db.rollback()
            ok, detail = False, str(exc)[:200]
        items.append(RedditBatchRetryItem(action_id=action_id, ok=ok, detail=detail))
        if ok:
            retried += 1
    return RedditBatchRetryOut(retried=retried, skipped=len(items) - retried, items=items)


@router.get("/automation/campaigns/{campaign_id}/report", response_model=RedditCampaignReportOut)
def campaign_daily_report(
    campaign_id: int,
    date: Optional[str] = Query(default=None, description="YYYY-MM-DD，默认今天（北京时间）"),
    db: Session = Depends(get_db),
    ctx: SiteContext = Depends(get_site_context),
):
    """Campaign 按日执行报告：按 planned_at 归属日统计配额达成、失败/跳过明细。"""
    campaign = _campaign_or_404(db, ctx, campaign_id)
    try:
        day = datetime.strptime(date, "%Y-%m-%d").date() if date else now_bj().date()
    except ValueError:
        raise HTTPException(status_code=400, detail="日期格式应为 YYYY-MM-DD")
    day_start = datetime(day.year, day.month, day.day)
    rows = (
        db.query(
            RedditCampaignAction.status,
            RedditCampaignAction.action_type,
            RedditCampaignAction.skip_reason,
            RedditCampaignAction.id,
        )
        .filter(
            RedditCampaignAction.campaign_id == campaign.id,
            RedditCampaignAction.planned_at.isnot(None),
            RedditCampaignAction.planned_at >= day_start,
            RedditCampaignAction.planned_at < day_start + timedelta(days=1),
        )
        .all()
    )
    by_status: dict[str, int] = {}
    by_type: dict[str, int] = {}
    skip_reasons: dict[str, int] = {}
    failed_ids: list[int] = []
    for st, at, reason, aid in rows:
        by_status[st] = by_status.get(st, 0) + 1
        by_type[at] = by_type.get(at, 0) + 1
        if st == "skipped" and reason:
            skip_reasons[reason] = skip_reasons.get(reason, 0) + 1
        if st == "failed":
            failed_ids.append(aid)
    return RedditCampaignReportOut(
        campaign_id=campaign.id,
        date=day,
        planned=len(rows),
        by_status=by_status,
        by_type=by_type,
        skip_reasons=skip_reasons,
        failed_action_ids=failed_ids,
    )
