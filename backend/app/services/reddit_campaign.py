"""Reddit 自动化 Campaign 调度引擎。

campaign_tick（beat 每 15 分钟）：
1. 扫描 running 且处于日期区间内的 campaign
2. 按当日配额 + 账号风控过滤 + 活跃时段随机排期，生成 post/comment/upvote 动作
3. 对账已有 post/comment 动作与内容的最终状态
4. 入队到期点赞动作（execute_campaign_action）

复用现有链路：
- post/comment 内容走 approved + scheduled_at → publish_scheduled_reddit_content（现有发布 beat）
- review 模式内容进现有待审队列
- 点赞走 RedditApiClient.vote 并落 RedditVoteLog（unique(account, thing) + upsert）
"""
from __future__ import annotations

import logging
import random
from datetime import datetime, timedelta
from app.core.timeutil import from_epoch_bj, now_bj
from types import SimpleNamespace

from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.client_site import ClientSite
from app.models.reddit import (
    RedditAccountProfile,
    RedditCampaign,
    RedditCampaignAction,
    RedditComment,
    RedditCommunity,
    RedditPost,
    RedditProduct,
    RedditVoteLog,
)
from app.models.social import SocialAccount
from app.services.ai_writer import DeepSeekError
from app.services.reddit_client import (
    RedditActionRetryable,
    RedditApiError,
    get_reddit_client_for_account,
    reddit_retry_delay,
)
from app.services.reddit_risk import (
    check_comment_publish,
    check_post_publish,
    get_account_daily_usage,
    get_or_create_profile,
)

logger = logging.getLogger(__name__)

# ============ 随机化与风控参数 ============
MIN_ACTION_GAP_MINUTES = 25      # 同账号相邻动作最小间隔（规避机器人特征）
MAX_CONTENT_PER_TICK = 2         # 每 campaign 每 tick 最多生成内容条数（控制 AI 成本与 tick 耗时）
MAX_UPVOTE_PLANS_PER_TICK = 5    # 每 campaign 每 tick 最多计划的点赞动作数
STALE_EXECUTING_MINUTES = 15     # executing 僵尸回收阈值（分钟）
UPVOTE_RELATED_MAX_AGE_DAYS = 7  # 相关帖时间窗口
UPVOTE_RELATED_SCORE_MIN = 2     # 过滤无人问津的死帖
UPVOTE_RELATED_SCORE_MAX = 50    # 过滤已爆帖（赞了没收益）
SELF_UPVOTE_MIN_AGE_MINUTES = 10  # 帖子发布至少 10 分钟后才安排自赞
# DeepSeek 成本控制：单站点 campaign 内容生成日限额（Redis INCRBY）
AI_DAILY_LIMIT_PER_SITE = 40

_UPVOTE_TYPES = ("upvote_related", "upvote_self")
_CONTENT_TYPES = ("post", "comment")


# ============ 时间窗口 ============
def _utc_day_start(now: datetime) -> datetime:
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


def _in_active_window(hour: int, start: int, end: int) -> bool:
    """活跃时段（北京时间小时，含端点）；start > end 视为跨午夜窗口。"""
    if start <= end:
        return start <= hour <= end
    return hour >= start or hour <= end


def _next_active_time(now: datetime, hour_start: int, hour_end: int) -> datetime:
    """把时间裁剪到活跃时段；不在窗口内则顺延到下一个窗口起点。"""
    if _in_active_window(now.hour, hour_start, hour_end):
        return now
    for add_days in (0, 1):
        candidate = (now + timedelta(days=add_days)).replace(
            hour=hour_start % 24, minute=0, second=0, microsecond=0
        )
        if candidate > now:
            return candidate
    return now


def _plan_time(db: Session, account_id: int, now: datetime, hour_start: int, hour_end: int) -> datetime:
    """在活跃窗口剩余时段内均匀随机取执行时间，并保证同账号动作最小间隔。"""
    if _in_active_window(now.hour, hour_start, hour_end):
        start_t = now
    else:
        start_t = _next_active_time(now, hour_start, hour_end)  # 下一个窗口起点
    end_t = start_t.replace(hour=hour_end % 24, minute=59, second=59, microsecond=0)
    if end_t <= start_t:
        end_t += timedelta(days=1)  # 跨午夜窗口（start > end）
    t = start_t + timedelta(seconds=random.uniform(0, (end_t - start_t).total_seconds()))
    last = (
        db.query(RedditCampaignAction.planned_at)
        .filter(
            RedditCampaignAction.account_id == account_id,
            RedditCampaignAction.status.in_(("pending", "executing")),
            RedditCampaignAction.planned_at.isnot(None),
        )
        .order_by(RedditCampaignAction.planned_at.desc())
        .limit(1)
        .scalar()
    )
    if last:
        floor = last + timedelta(minutes=MIN_ACTION_GAP_MINUTES)
        if t < floor:
            t = floor + timedelta(minutes=random.uniform(0, 5))
    return t


# ============ 配额与账号池 ============
def _quota_used(db: Session, campaign_id: int, now: datetime) -> dict[str, int]:
    """当日已计划的动作数（排除 skipped），以建库统计为准、抗重启。"""
    day_start = _utc_day_start(now)
    rows = (
        db.query(RedditCampaignAction.action_type, func.count(RedditCampaignAction.id))
        .filter(
            RedditCampaignAction.campaign_id == campaign_id,
            RedditCampaignAction.status != "skipped",
            RedditCampaignAction.created_at >= day_start,
        )
        .group_by(RedditCampaignAction.action_type)
        .all()
    )
    counts = {t: int(c) for t, c in rows}
    return {
        "posts": counts.get("post", 0),
        "comments": counts.get("comment", 0),
        "upvotes": counts.get("upvote_related", 0) + counts.get("upvote_self", 0),
    }


def _pool_accounts(db: Session, campaign: RedditCampaign) -> list[SocialAccount]:
    """账号池：活跃 + 本站 Reddit 号 + 风控状态 normal，随机打散。"""
    ids = [int(a) for a in (campaign.account_ids or []) if a]
    q = db.query(SocialAccount).filter(
        SocialAccount.site_id == campaign.site_id,
        SocialAccount.platform == "reddit",
        SocialAccount.is_active.is_(True),
    )
    if ids:
        q = q.filter(SocialAccount.id.in_(ids))
    accounts = q.all()
    if not accounts:
        return []
    profiles = {
        p.account_id: p
        for p in db.query(RedditAccountProfile)
        .filter(RedditAccountProfile.site_id == campaign.site_id)
        .all()
    }
    pool: list[SocialAccount] = []
    for acc in accounts:
        profile = profiles.get(acc.id) or get_or_create_profile(db, campaign.site_id, acc.id)
        if profile.risk_status == "normal":
            pool.append(acc)
    random.shuffle(pool)
    return pool


def _target_subreddits(db: Session, campaign: RedditCampaign, account: SocialAccount) -> list[str]:
    """发帖/互动目标社区：campaign 配置 → 账号人设社区 → 站点产品社区。"""
    from app.services.reddit_client import normalize_subreddit

    names = [normalize_subreddit(s) for s in (campaign.subreddits or []) if s]
    if names:
        return names
    rows = (
        db.query(RedditCommunity.name)
        .filter(
            RedditCommunity.site_id == campaign.site_id,
            RedditCommunity.is_active.is_(True),
            RedditCommunity.purpose == "persona",
            RedditCommunity.account_id == account.id,
        )
        .all()
    )
    if rows:
        return [r[0] for r in rows]
    rows = (
        db.query(RedditCommunity.name)
        .filter(
            RedditCommunity.site_id == campaign.site_id,
            RedditCommunity.is_active.is_(True),
            RedditCommunity.purpose == "promo",
        )
        .all()
    )
    return [r[0] for r in rows]


# ============ AI 成本控制 ============
def _ai_budget_key(site_id: int) -> str:
    return f"reddit:campaign:ai:{site_id}:{now_bj():%Y%m%d}"


def _ai_budget_available(site_id: int) -> tuple[bool, int]:
    from app.utils.rate_limiter import get_redis

    try:
        used = int(get_redis().get(_ai_budget_key(site_id)) or 0)
    except Exception:
        logger.debug("ai budget check failed", exc_info=True)
        return True, 0
    return used < AI_DAILY_LIMIT_PER_SITE, used


def _ai_budget_consume(site_id: int, n: int = 1) -> None:
    from app.utils.rate_limiter import get_redis

    try:
        r = get_redis()
        key = _ai_budget_key(site_id)
        r.incrby(key, n)
        r.expire(key, 172800)
    except Exception:
        logger.debug("ai budget consume failed", exc_info=True)


# ============ 动作写入 ============
def _insert_action(db: Session, **kwargs) -> RedditCampaignAction | None:
    """插入动作记录；unique(campaign_id, dedup_key) 冲突时返回 None（防重复计划）。"""
    action = RedditCampaignAction(**kwargs)
    db.add(action)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return None
    db.refresh(action)
    return action


def _finish_action(
    db: Session,
    action: RedditCampaignAction,
    status: str,
    *,
    error_message: str | None = None,
    skip_reason: str | None = None,
) -> None:
    action.status = status
    action.executed_at = now_bj()
    action.error_message = error_message
    action.skip_reason = skip_reason
    db.commit()


# ============ 发帖动作 ============
def _create_campaign_post(
    db: Session, campaign: RedditCampaign, account: SocialAccount, planned_at: datetime
) -> tuple[RedditCampaignAction | None, str | None]:
    """生成并落库一条 campaign 帖子；返回 (action, skip_reason)。

    auto 模式：approved + scheduled_at → 现有发布链路自动消费；风控不过则降级待审。
    review 模式：pending_review 进待审队列。
    """
    from app.services.reddit_content import generate_post_content

    site = db.get(ClientSite, campaign.site_id)
    subreddits = _target_subreddits(db, campaign, account)
    if not subreddits:
        return None, None  # 无目标社区：静默跳过（配置问题，避免垃圾记录）
    subreddit = random.choice(subreddits)

    community = (
        db.query(RedditCommunity)
        .filter(RedditCommunity.site_id == campaign.site_id, RedditCommunity.name == subreddit)
        .first()
    )
    allow_product = False
    if campaign.product_id and community and community.purpose == "promo" and community.allows_links:
        allow_product = (
            db.query(RedditProduct)
            .filter(RedditProduct.id == campaign.product_id, RedditProduct.is_active.is_(True))
            .first()
            is not None
        )

    try:
        generated = generate_post_content(
            db,
            site=site,
            account=account,
            subreddit=subreddit,
            allow_product=allow_product,
            include_site_url=allow_product,
            product_id=campaign.product_id,
        )
    except DeepSeekError as exc:
        logger.warning("Campaign post AI failed campaign=%s account=%s: %s", campaign.id, account.id, exc)
        return None, None  # AI 瞬时故障：不记录，下个 tick 重试
    except ValueError as exc:
        return (
            _insert_action(
                db,
                campaign_id=campaign.id,
                site_id=campaign.site_id,
                account_id=account.id,
                action_type="post",
                status="skipped",
                skip_reason=str(exc)[:200],
                dedup_key=f"post:{campaign.id}:{account.id}:{planned_at:%Y%m%d}",
            ),
            None,
        )

    post = RedditPost(
        site_id=campaign.site_id,
        account_id=account.id,
        post_type="auto",
        subreddit=subreddit,
        keyword=generated["keyword"],
        title=generated["title"],
        body=generated["body"],
        site_url=generated["site_url"],
        content_intent=generated["intent"],
        campaign_id=campaign.id,
    )
    dedup_key = f"post:{campaign.id}:{account.id}:{planned_at:%Y%m%d}"

    if campaign.content_mode == "auto":
        report = check_post_publish(
            db,
            site_id=campaign.site_id,
            account_id=account.id,
            subreddit=subreddit,
            body=post.body,
            site_url=post.site_url,
        )
        if report.ok:
            post.status = "approved"
            post.scheduled_at = planned_at
        else:
            # 风控不过：降级进待审队列人工兜底，动作记 skipped
            post.status = "pending_review"
    else:
        post.status = "pending_review"

    db.add(post)
    db.commit()
    db.refresh(post)

    if campaign.content_mode == "auto" and post.status == "pending_review":
        action = _insert_action(
            db,
            campaign_id=campaign.id,
            site_id=campaign.site_id,
            account_id=account.id,
            action_type="post",
            status="skipped",
            skip_reason=(report.errors[0] if report.errors else "risk_blocked")[:200],
            post_id=post.id,
            planned_at=planned_at,
            dedup_key=dedup_key,
        )
        return action, None

    action = _insert_action(
        db,
        campaign_id=campaign.id,
        site_id=campaign.site_id,
        account_id=account.id,
        action_type="post",
        status="pending",
        post_id=post.id,
        planned_at=planned_at,
        dedup_key=dedup_key,
    )
    if action is None:
        # 并发下 dedup 冲突：内容保留（review 可审 / auto 会按点发布），不重复计动作
        return None, None
    return action, None


# ============ 评论动作 ============
def _comment_candidates(
    db: Session, campaign: RedditCampaign, account: SocialAccount, limit: int = 4
) -> list[dict]:
    """自动发现评论目标：campaign 社区/人设社区中未互动过的讨论帖。"""
    subs = _target_subreddits(db, campaign, account)
    if not subs:
        return []
    terms: list[str] = []
    if campaign.product_id:
        product = db.get(RedditProduct, campaign.product_id)
        if product:
            from app.services.reddit_discover import product_search_terms

            terms = product_search_terms(keywords=[r.keyword for r in (product.keyword_rows or [])])

    seen_things = {
        row[0]
        for row in db.query(RedditComment.target_thing_id)
        .filter(
            RedditComment.account_id == account.id,
            RedditComment.site_id == campaign.site_id,
        )
        .all()
    }
    seen_things |= {
        row[0]
        for row in db.query(RedditCampaignAction.target_thing_id)
        .filter(
            RedditCampaignAction.site_id == campaign.site_id,
            RedditCampaignAction.account_id == account.id,
            RedditCampaignAction.action_type == "comment",
        )
        .all()
    }

    client = get_reddit_client_for_account(account)
    candidates: list[dict] = []
    for sub in random.sample(subs, min(len(subs), 3)):
        try:
            if terms:
                keyword = random.choice(terms)
                items = client.search_posts(sub, keyword, limit=8)
            else:
                keyword = ""
                items = client.list_feed(sub, limit=8)
        except RedditApiError as exc:
            logger.warning("Campaign comment discovery failed sub=%s: %s", sub, exc)
            continue
        for item in items:
            thing_id = str(item.get("thing_id") or "")
            url = item.get("url") or ""
            if not thing_id or not url or thing_id in seen_things:
                continue
            candidates.append(
                {
                    "url": url,
                    "thing_id": thing_id,
                    "title": item.get("title") or "",
                    "body": item.get("body") or "",
                    "subreddit": item.get("subreddit") or sub,
                    "keyword": keyword,
                }
            )
            if len(candidates) >= limit:
                return candidates
    return candidates


def _create_campaign_comment(
    db: Session, campaign: RedditCampaign, account: SocialAccount, planned_at: datetime, candidate: dict
) -> tuple[RedditCampaignAction | None, str | None]:
    """为候选帖生成评论并落库（复用 API 层生成链路）。"""
    from fastapi import HTTPException

    from app.api.v1.reddit import _generate_comment_for_url
    from app.services.reddit_risk import check_comment_publish

    site = db.get(ClientSite, campaign.site_id)
    ctx = SimpleNamespace(site=site)
    try:
        out = _generate_comment_for_url(
            db,
            ctx,
            account.id,
            candidate["url"],
            include_site_url=False,  # campaign 评论为纯互动，不带链接（链接策略由发帖承担）
            discover_source="campaign",
            keyword=candidate.get("keyword") or None,
            known_title=candidate.get("title"),
            known_body=candidate.get("body"),
        )
    except DeepSeekError as exc:
        logger.warning("Campaign comment AI failed campaign=%s: %s", campaign.id, exc)
        return None, None
    except HTTPException as exc:
        logger.warning("Campaign comment generate rejected campaign=%s: %s", campaign.id, exc.detail)
        return None, None

    comment = db.query(RedditComment).filter(RedditComment.id == out.id).first()
    if comment is None:
        return None, None
    comment.campaign_id = campaign.id
    dedup_key = f"comment:{campaign.id}:{account.id}:{candidate['thing_id']}"

    status = "pending"
    skip_reason = None
    if campaign.content_mode == "auto":
        report = check_comment_publish(
            db, site_id=campaign.site_id, account_id=account.id, body=comment.body
        )
        if report.ok:
            comment.status = "approved"
            comment.scheduled_at = planned_at
        else:
            comment.status = "pending_review"  # 风控不过：留待审人工兜底
            status = "skipped"
            skip_reason = (report.errors[0] if report.errors else "risk_blocked")[:200]
    db.commit()
    db.refresh(comment)

    action = _insert_action(
        db,
        campaign_id=campaign.id,
        site_id=campaign.site_id,
        account_id=account.id,
        action_type="comment",
        status=status,
        comment_id=comment.id,
        target_thing_id=candidate["thing_id"],
        target_post_url=candidate["url"],
        planned_at=planned_at,
        dedup_key=dedup_key,
    )
    if action is None:
        return None, None
    return action, skip_reason


# ============ 点赞动作 ============
def _site_voted_things(db: Session, site_id: int) -> set[str]:
    return {
        row[0]
        for row in db.query(RedditVoteLog.thing_id)
        .filter(RedditVoteLog.site_id == site_id, RedditVoteLog.direction == 1)
        .all()
    }


def _site_actioned_things(db: Session, site_id: int) -> set[str]:
    return {
        row[0]
        for row in db.query(RedditCampaignAction.target_thing_id)
        .filter(
            RedditCampaignAction.site_id == site_id,
            RedditCampaignAction.action_type.in_(_UPVOTE_TYPES),
            RedditCampaignAction.target_thing_id.isnot(None),
        )
        .all()
    }


def _plan_related_upvotes(
    db: Session,
    campaign: RedditCampaign,
    pool: list[SocialAccount],
    now: datetime,
    remaining: int,
    hour_start: int,
    hour_end: int,
) -> int:
    """相关讨论帖点赞（养号互动）：搜索候选 → 过滤 → 随机账号计划投票。"""
    if remaining <= 0 or not pool:
        return 0
    searcher = random.choice(pool)
    client = get_reddit_client_for_account(searcher)
    subs = _target_subreddits(db, campaign, searcher)
    if not subs:
        return 0

    terms: list[str] = []
    if campaign.product_id:
        product = db.get(RedditProduct, campaign.product_id)
        if product:
            from app.services.reddit_discover import product_search_terms

            terms = product_search_terms(keywords=[r.keyword for r in (product.keyword_rows or [])])

    excluded = _site_voted_things(db, campaign.site_id) | _site_actioned_things(db, campaign.site_id)
    pool_names = {(a.account_name or "").lower() for a in pool}
    cutoff = now_bj() - timedelta(days=UPVOTE_RELATED_MAX_AGE_DAYS)
    candidates: list[dict] = []
    for sub in random.sample(subs, min(len(subs), 3)):
        try:
            if terms:
                keyword = random.choice(terms)
                items = client.search_posts(sub, keyword, limit=10)
            else:
                keyword = ""
                items = client.list_feed(sub, limit=10)
        except RedditApiError as exc:
            logger.warning("Campaign upvote discovery failed sub=%s: %s", sub, exc)
            continue
        for item in items:
            thing_id = str(item.get("thing_id") or "")
            url = item.get("url") or ""
            if not thing_id or not url or thing_id in excluded:
                continue
            author = (item.get("author") or "").lower()
            if author and author in pool_names:
                continue  # 池内互赞自己人帖子无意义
            score = int(item.get("score") or 0)
            if not (UPVOTE_RELATED_SCORE_MIN <= score <= UPVOTE_RELATED_SCORE_MAX):
                continue
            created_utc = int(item.get("created_utc") or 0)
            if created_utc and from_epoch_bj(created_utc) < cutoff:
                continue
            candidates.append({"thing_id": thing_id, "url": url})
            if len(candidates) >= remaining * 2:
                break
        if len(candidates) >= remaining * 2:
            break

    planned = 0
    for cand in candidates:
        if planned >= remaining or planned >= MAX_UPVOTE_PLANS_PER_TICK:
            break
        voter = random.choice(pool)
        action = _insert_action(
            db,
            campaign_id=campaign.id,
            site_id=campaign.site_id,
            account_id=voter.id,
            action_type="upvote_related",
            status="pending",
            target_thing_id=cand["thing_id"],
            target_post_url=cand["url"],
            planned_at=_plan_time(db, voter.id, now, hour_start, hour_end),
            dedup_key=f"upvote:{voter.id}:{cand['thing_id']}",
        )
        if action:
            planned += 1
    return planned


def _plan_self_upvotes(
    db: Session,
    campaign: RedditCampaign,
    pool: list[SocialAccount],
    now: datetime,
    remaining: int,
    hour_start: int,
    hour_end: int,
) -> int:
    """自己帖子点赞（提热度）：池内其他账号投票（voter != author，规避 Reddit 同号自赞判定）。"""
    if not campaign.self_upvote_enabled or remaining <= 0 or len(pool) < 2:
        return 0
    delay = campaign.self_upvote_delay_minutes or 240
    earliest = now - timedelta(minutes=delay)
    latest = now - timedelta(minutes=SELF_UPVOTE_MIN_AGE_MINUTES)
    author_ids = [a.id for a in pool]
    posts = (
        db.query(RedditPost)
        .filter(
            RedditPost.site_id == campaign.site_id,
            RedditPost.status == "posted",
            RedditPost.reddit_post_id.isnot(None),
            RedditPost.published_at.isnot(None),
            RedditPost.published_at >= earliest,
            RedditPost.published_at <= latest,
            RedditPost.account_id.in_(author_ids),
        )
        .order_by(RedditPost.published_at.desc())
        .limit(20)
        .all()
    )

    existing_dedups = {
        row[0]
        for row in db.query(RedditCampaignAction.dedup_key)
        .filter(RedditCampaignAction.campaign_id == campaign.id)
        .all()
    }
    planned = 0
    for post in posts:
        if planned >= remaining:
            break
        voters = [a for a in pool if a.id != post.account_id]
        random.shuffle(voters)
        for voter in voters:
            dedup_key = f"upvote_self:{voter.id}:{post.id}"
            if dedup_key in existing_dedups:
                continue
            already = (
                db.query(RedditVoteLog.id)
                .filter(
                    RedditVoteLog.account_id == voter.id,
                    RedditVoteLog.thing_id == post.reddit_post_id,
                    RedditVoteLog.direction == 1,
                )
                .first()
            )
            if already:
                continue
            action = _insert_action(
                db,
                campaign_id=campaign.id,
                site_id=campaign.site_id,
                account_id=voter.id,
                action_type="upvote_self",
                status="pending",
                target_thing_id=post.reddit_post_id,
                target_post_url=post.reddit_permalink,
                planned_at=_plan_time(db, voter.id, now, hour_start, hour_end),
                dedup_key=dedup_key,
            )
            if action:
                existing_dedups.add(dedup_key)
                planned += 1
                break  # 每帖每 tick 只安排一个赞
    return planned


# ============ 对账与执行 ============
def reconcile_campaign_actions(db: Session, campaign_id: int | None = None, limit: int = 100) -> dict:
    """post/comment 动作与内容状态对账：posted→done / failed→failed / rejected→skipped。

    仅处理已关联内容的动作；无内容引用的 pending 动作（异常孤儿）保持原状，
    避免影响当日配额统计。
    """
    q = db.query(RedditCampaignAction).filter(
        RedditCampaignAction.status == "pending",
        RedditCampaignAction.action_type.in_(_CONTENT_TYPES),
        (RedditCampaignAction.post_id.isnot(None)) | (RedditCampaignAction.comment_id.isnot(None)),
    )
    if campaign_id:
        q = q.filter(RedditCampaignAction.campaign_id == campaign_id)
    changed = {"done": 0, "failed": 0, "skipped": 0}
    for action in q.limit(limit).all():
        content = None
        if action.post_id:
            content = db.get(RedditPost, action.post_id)
        elif action.comment_id:
            content = db.get(RedditComment, action.comment_id)
        if content is None:
            _finish_action(db, action, "skipped", skip_reason="content_deleted")
            changed["skipped"] += 1
        elif content.status == "posted":
            _finish_action(db, action, "done")
            changed["done"] += 1
        elif content.status == "failed":
            _finish_action(db, action, "failed", error_message=(content.error_message or "发布失败")[:500])
            changed["failed"] += 1
        elif content.status == "rejected":
            _finish_action(db, action, "skipped", skip_reason="content_rejected")
            changed["skipped"] += 1
    return changed


def recover_stale_actions(db: Session, timeout_minutes: int = STALE_EXECUTING_MINUTES) -> int:
    """回收 executing 超时的僵尸动作 → failed（可手动重试）。"""
    cutoff = now_bj() - timedelta(minutes=timeout_minutes)
    stale = (
        db.query(RedditCampaignAction)
        .filter(
            RedditCampaignAction.status == "executing",
            RedditCampaignAction.updated_at <= cutoff,
        )
        .all()
    )
    for action in stale:
        action.status = "failed"
        action.error_message = "执行超时，可重试"
    if stale:
        db.commit()
    return len(stale)


def due_upvote_action_ids(db: Session, limit: int = 30) -> list[int]:
    """到期待执行的点赞动作 ID。"""
    rows = (
        db.query(RedditCampaignAction.id)
        .filter(
            RedditCampaignAction.status == "pending",
            RedditCampaignAction.action_type.in_(_UPVOTE_TYPES),
            RedditCampaignAction.planned_at.isnot(None),
            RedditCampaignAction.planned_at <= now_bj(),
        )
        .order_by(RedditCampaignAction.planned_at.asc())
        .limit(limit)
        .all()
    )
    return [r[0] for r in rows]


def claim_action(db: Session, action_id: int) -> bool:
    """原子抢占：pending → executing（防重复执行）。"""
    rows = (
        db.query(RedditCampaignAction)
        .filter(
            RedditCampaignAction.id == action_id,
            RedditCampaignAction.status == "pending",
        )
        .update(
            {"status": "executing", "updated_at": now_bj()},
            synchronize_session="fetch",
        )
    )
    db.commit()
    return rows == 1


# 限流/熔断自动重试上限（attempt 0-based：0/1 重试，2 耗尽标 failed）
_ACTION_AUTO_RETRIES = 2


def execute_action(db: Session, action_id: int, attempt: int = 0) -> dict:
    """执行单条点赞动作：claim → vote → 落 RedditVoteLog（upsert）→ done。

    遇 429 限流/503 熔断：动作回 pending + planned_at 前移，抛 RedditActionRetryable
    由任务层延迟重试；重试耗尽（attempt >= _ACTION_AUTO_RETRIES）标 failed 留痕。
    """
    if not claim_action(db, action_id):
        return {"ok": False, "reason": "not_claimable"}
    action = db.get(RedditCampaignAction, action_id)
    if action is None:
        return {"ok": False, "reason": "missing"}
    try:
        profile = get_or_create_profile(db, action.site_id, action.account_id)
        if profile.risk_status != "normal":
            _finish_action(db, action, "skipped", skip_reason="account_risk_warning")
            return {"ok": False, "reason": "account_risk_warning"}

        if not action.target_thing_id:
            _finish_action(db, action, "failed", error_message="缺少目标 thing_id")
            return {"ok": False, "reason": "missing_target"}

        account = db.query(SocialAccount).filter(SocialAccount.id == action.account_id).first()
        if account is None:
            _finish_action(db, action, "failed", error_message="账号不存在")
            return {"ok": False, "reason": "missing_account"}

        client = get_reddit_client_for_account(account)
        client.vote(action.target_thing_id, 1)

        log = (
            db.query(RedditVoteLog)
            .filter(RedditVoteLog.account_id == action.account_id, RedditVoteLog.thing_id == action.target_thing_id)
            .first()
        )
        if log:
            log.direction = 1
            log.source = "campaign"
            log.campaign_action_id = action.id
            log.error_message = None
        else:
            db.add(
                RedditVoteLog(
                    site_id=action.site_id,
                    account_id=action.account_id,
                    thing_id=action.target_thing_id,
                    direction=1,
                    source="campaign",
                    campaign_action_id=action.id,
                )
            )
        _finish_action(db, action, "done")
        return {"ok": True, "action_id": action_id}
    except RedditApiError as exc:
        wait = reddit_retry_delay(exc)
        db.rollback()
        action = db.get(RedditCampaignAction, action_id)
        if wait is not None and attempt < _ACTION_AUTO_RETRIES and action is not None:
            # 回 pending + planned_at 前移：Celery retry 与 tick 扫描双保险
            action.status = "pending"
            action.planned_at = now_bj() + timedelta(seconds=wait)
            action.error_message = (
                f"限流({exc.status_code})，{wait}s 后自动重试"
                f"（第 {attempt + 1}/{_ACTION_AUTO_RETRIES} 次）：{str(exc)[:300]}"
            )
            db.commit()
            raise RedditActionRetryable(
                str(exc)[:300], status_code=exc.status_code or 429, retry_after=wait, attempt=attempt
            ) from exc
        if action is not None:
            _finish_action(db, action, "failed", error_message=str(exc)[:500])
        return {"ok": False, "reason": str(exc)[:200]}
    except Exception as exc:  # noqa: BLE001 — 兜底防僵尸 executing
        db.rollback()
        action = db.get(RedditCampaignAction, action_id)
        if action is not None:
            _finish_action(db, action, "failed", error_message=str(exc)[:500])
        return {"ok": False, "reason": str(exc)[:200]}


# ============ tick 主流程 ============
def _plan_posts(db: Session, campaign: RedditCampaign, pool, now, remaining, hour_start, hour_end, stats) -> int:
    created = 0
    ai_budget, _ = _ai_budget_available(campaign.site_id)
    for account in pool:
        if created >= remaining or created >= MAX_CONTENT_PER_TICK or not ai_budget:
            break
        profile = get_or_create_profile(db, campaign.site_id, account.id)
        usage = get_account_daily_usage(db, account.id)
        if profile.daily_post_limit and usage["posts"] >= profile.daily_post_limit:
            stats["skipped"] += 1
            continue
        action, _skip = _create_campaign_post(
            db, campaign, account, planned_at=_plan_time(db, account.id, now, hour_start, hour_end)
        )
        if action is None:
            continue  # 瞬时故障或 dedup 冲突，下个 tick 重试
        _ai_budget_consume(campaign.site_id)
        ai_budget, _ = _ai_budget_available(campaign.site_id)
        if action.status == "pending":
            created += 1
        else:
            stats["skipped"] += 1  # 风控降级等场景：内容已留待审，动作不计数
    return created


def _plan_comments(db: Session, campaign: RedditCampaign, pool, now, remaining, hour_start, hour_end, stats) -> int:
    created = 0
    ai_budget, _ = _ai_budget_available(campaign.site_id)
    for account in pool:
        if created >= remaining or created >= MAX_CONTENT_PER_TICK or not ai_budget:
            break
        profile = get_or_create_profile(db, campaign.site_id, account.id)
        usage = get_account_daily_usage(db, account.id)
        if profile.daily_comment_limit and usage["comments"] >= profile.daily_comment_limit:
            stats["skipped"] += 1
            continue
        candidates = _comment_candidates(db, campaign, account)
        if not candidates:
            continue
        candidate = random.choice(candidates)
        action, _skip = _create_campaign_comment(
            db,
            campaign,
            account,
            planned_at=_plan_time(db, account.id, now, hour_start, hour_end),
            candidate=candidate,
        )
        if action is None:
            continue
        created += 1
        _ai_budget_consume(campaign.site_id)
        ai_budget, _ = _ai_budget_available(campaign.site_id)
    return created


def _tick_campaign(db: Session, campaign: RedditCampaign, now: datetime) -> dict:
    stats = {"posts": 0, "comments": 0, "upvotes": 0, "skipped": 0}
    pool = _pool_accounts(db, campaign)
    if not pool:
        return stats
    hour_start = int(campaign.active_hour_start or 0)
    hour_end = int(23 if campaign.active_hour_end is None else campaign.active_hour_end)
    quota = _quota_used(db, campaign.id, now)

    remaining_posts = max(0, int(campaign.daily_posts or 0) - quota["posts"])
    remaining_comments = max(0, int(campaign.daily_comments or 0) - quota["comments"])
    remaining_upvotes = max(0, int(campaign.daily_upvotes or 0) - quota["upvotes"])

    if remaining_posts > 0:
        stats["posts"] = _plan_posts(db, campaign, pool, now, remaining_posts, hour_start, hour_end, stats)
    if remaining_comments > 0:
        stats["comments"] = _plan_comments(db, campaign, pool, now, remaining_comments, hour_start, hour_end, stats)
    if remaining_upvotes > 0:
        planned = _plan_related_upvotes(db, campaign, pool, now, remaining_upvotes, hour_start, hour_end)
        planned += _plan_self_upvotes(
            db, campaign, pool, now, remaining_upvotes - planned, hour_start, hour_end
        )
        stats["upvotes"] = planned
    return stats


def run_campaign_tick(db: Session) -> dict:
    """tick 主入口：扫描活跃 campaign → 计划动作 → 对账 → 回收僵尸。"""
    now = now_bj()
    today = now.date()
    campaigns = db.query(RedditCampaign).filter(RedditCampaign.status == "running").all()
    active = [c for c in campaigns if c.start_date <= today <= c.end_date]

    reconcile_campaign_actions(db)
    recovered = recover_stale_actions(db)
    if recovered:
        logger.info("Campaign tick recovered %d stale actions", recovered)

    summary = {"campaigns": len(active), "posts": 0, "comments": 0, "upvotes": 0, "skipped": 0}
    for campaign in active:
        campaign_id = campaign.id
        try:
            stats = _tick_campaign(db, campaign, now)
            # last_tick_at 提交放在 try 内：campaign 可能被 API 并发删除（0 行匹配 → StaleDataError）
            campaign.last_tick_at = now
            db.commit()
        except Exception:  # noqa: BLE001 — 单 campaign 失败不阻断其他
            db.rollback()
            logger.exception("Campaign tick failed campaign=%s", campaign_id)
            continue
        for key in ("posts", "comments", "upvotes", "skipped"):
            summary[key] += stats.get(key, 0)
        if any(stats.values()):
            logger.info(
                "Campaign #%s tick: posts=%s comments=%s upvotes=%s skipped=%s",
                campaign.id,
                stats["posts"],
                stats["comments"],
                stats["upvotes"],
                stats["skipped"],
            )
    return summary
