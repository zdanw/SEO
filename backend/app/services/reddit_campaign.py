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

import json
import logging
import os
import random
import socket
from datetime import datetime, timedelta
from fastapi import HTTPException
from app.core.config import settings
from app.core.timeutil import as_bj_naive, from_epoch_bj, now_bj
from types import SimpleNamespace

from sqlalchemy import func, text
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
PLANNING_SPAN_HOURS = 4          # 当日排期规划跨度：配额摊在"从现在起 N 小时"内采样，
                                 # 避免均匀铺满整个窗口导致动作间隔动辄 1-3 小时
MAX_CONTENT_PER_TICK = 2         # 每 campaign 每 tick 最多生成内容条数（控制 AI 成本与 tick 耗时）
MAX_UPVOTE_PLANS_PER_TICK = 5    # 每 campaign 每 tick 最多计划的点赞动作数
STALE_EXECUTING_MINUTES = 15     # executing 僵尸回收阈值（分钟）——仅对无租约的遗留行兜底
EXECUTE_LEASE_MINUTES = 10       # 执行租约时长（> 动作硬超时 70s，留崩溃恢复余量）
GENERATION_LEASE_MINUTES = 20    # 生成租约时长（AI 流水线检测+humanize 多轮重写，显著长于执行）
# 发现出环：评论候选 Redis 缓存 TTL / 发现任务账号级最小间隔
CANDIDATE_CACHE_TTL_SECONDS = 6 * 3600
CAMPAIGN_DISCOVERY_MIN_INTERVAL_SECONDS = 600
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


def _can_plan_today(now: datetime, hour_start: int, hour_end: int) -> bool:
    """今天的活跃窗口是否还有排期余地（窗口已过则今天不再排新动作）。"""
    return _next_active_time(now, hour_start, hour_end).date() == now.date()


def _free_intervals(
    start: datetime, end: datetime, busy: list[datetime], gap: timedelta
) -> list[tuple[datetime, datetime]]:
    """[start, end] 内扣除每个 busy 点 ±gap 后的空闲区间列表（升序）。"""
    blocked = sorted((p - gap, p + gap) for p in busy)
    free: list[tuple[datetime, datetime]] = []
    cur = start
    for bs, be in blocked:
        if be <= cur:
            continue
        if bs >= end:
            break
        if bs > cur:
            free.append((cur, bs))
        cur = max(cur, be)
        if cur >= end:
            break
    if cur < end:
        free.append((cur, end))
    return free


def _plan_time(db: Session, account_id: int, now: datetime, hour_start: int, hour_end: int) -> datetime:
    """在活跃窗口剩余时段内均匀随机取执行时间，并保证同账号动作最小间隔。

    间隔为双向校验：新动作与账号任一已排（pending/executing）动作的前后间隔都
    不得小于 MIN_ACTION_GAP_MINUTES——否则单个排在窗口尾部的 pending 动作会把
    当天后续排期全部顶出窗口（排期饿死）。今日窗口无空档时顺延到下一个窗口
    起点（跨天由调用方按“不跨天排期”规则跳过）。

    忙碌区间按**账号全局**查询、刻意不过滤 campaign_id：同一账号在多个
    campaign 之间的动作同样必须保持最小间隔（账号级风控硬约束）。若按
    campaign 过滤，两个共用账号的 campaign 会把动作排到相邻分钟，形成
    机器人特征——勿改。
    """
    gap = timedelta(minutes=MIN_ACTION_GAP_MINUTES)
    if _in_active_window(now.hour, hour_start, hour_end):
        start_t = now
    else:
        start_t = _next_active_time(now, hour_start, hour_end)  # 下一个窗口起点

    for _ in range(3):
        end_t = start_t.replace(hour=hour_end % 24, minute=59, second=59, microsecond=0)
        if end_t <= start_t:
            end_t += timedelta(days=1)  # 跨午夜窗口（start > end）
        # 规划跨度：只在 [start_t, min(窗口尾, start_t+N小时)] 内采样，间隔更紧凑；
        # 窗口剩余不足 N 小时则用整个剩余窗口
        span_end = start_t + timedelta(hours=PLANNING_SPAN_HOURS)
        if end_t > span_end:
            end_t = span_end
        busy = [
            as_bj_naive(p)
            for (p,) in db.query(RedditCampaignAction.planned_at)
            .filter(
                RedditCampaignAction.account_id == account_id,
                RedditCampaignAction.status.in_(("pending", "executing")),
                RedditCampaignAction.planned_at.isnot(None),
                RedditCampaignAction.planned_at >= start_t - gap,
                RedditCampaignAction.planned_at <= end_t + gap,
            )
            .all()
        ]
        # timestamptz 列在 Postgres 读回带 +08 tzinfo，统一转北京墙钟 naive 再比较
        free = _free_intervals(start_t, end_t, busy, gap)
        if free:
            # 按空档长度加权选空档，再在空档内均匀取点
            total = sum((e - s).total_seconds() for s, e in free)
            pick = random.uniform(0, total)
            acc = 0.0
            for s, e in free:
                span = (e - s).total_seconds()
                if acc + span >= pick:
                    return s + timedelta(seconds=random.uniform(0, span))
                acc += span
        # 今日窗口已被占满：顺延到下一个窗口起点重试
        start_t = _next_active_time(end_t + timedelta(seconds=1), hour_start, hour_end)
    return start_t  # 兜底：下一窗口起点（调用方跨天检查会跳过）


# ============ 配额与账号池 ============
def _quota_used(db: Session, campaign_id: int, now: datetime) -> dict[str, int]:
    """当日（按 planned_at 计划执行日归属）已计划的动作数（排除 skipped），以建库统计为准、抗重启。

    按 planned_at 而非 created_at 统计：昨天创建但排到今天的动作计入今天配额，
    今天排到明天窗口的动作计入明天配额——保证“每日一轮”语义，每天独立补足
    当天配额，不跨天挤占也不会重复排期。
    """
    day_start = _utc_day_start(now)
    rows = (
        db.query(RedditCampaignAction.action_type, func.count(RedditCampaignAction.id))
        .filter(
            RedditCampaignAction.campaign_id == campaign_id,
            RedditCampaignAction.status != "skipped",
            RedditCampaignAction.planned_at.isnot(None),
            RedditCampaignAction.planned_at >= day_start,
            RedditCampaignAction.planned_at < day_start + timedelta(days=1),
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


def _target_subreddits(
    db: Session,
    campaign: RedditCampaign,
    account: SocialAccount,
    *,
    include_promo: bool = False,
) -> list[str]:
    """发帖/互动目标社区：账号人设社区（+ 可选产品社区）。

    campaign.subreddits 只作为**白名单过滤**，不再覆盖账号人设社区：
    - 人设内容只发人设社区；产品内容（include_promo=True）才纳入产品社区
    - 白名单与社区库无交集时忽略白名单，绝不发到未入库的社区
    - 社区库为空时（旧配置）才回退到 campaign 显式列表
    """
    from app.services.reddit_client import normalize_subreddit

    persona = [
        r[0]
        for r in db.query(RedditCommunity.name)
        .filter(
            RedditCommunity.site_id == campaign.site_id,
            RedditCommunity.is_active.is_(True),
            RedditCommunity.purpose == "persona",
            RedditCommunity.account_id == account.id,
        )
        .all()
        if r[0]
    ]
    promo: list[str] = []
    if include_promo:
        promo = [
            r[0]
            for r in db.query(RedditCommunity.name)
            .filter(
                RedditCommunity.site_id == campaign.site_id,
                RedditCommunity.is_active.is_(True),
                RedditCommunity.purpose == "promo",
            )
            .all()
            if r[0]
        ]
    base = [normalize_subreddit(n) for n in (persona + promo) if n]
    cfg = [normalize_subreddit(s) for s in (campaign.subreddits or []) if s]
    if not base:
        return cfg
    if cfg:
        allowed = {s.lower() for s in cfg}
        filtered = [s for s in base if s.lower() in allowed]
        return filtered or base
    return base


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


# ============ 发现限流退避（账号级 429 时跳过本轮候选发现）============
DISCOVERY_BACKOFF_SECONDS = 30 * 60


def _discovery_paused(account_id: int) -> bool:
    from app.utils.rate_limiter import get_redis

    try:
        return bool(get_redis().get(f"backoff:reddit:discovery:{account_id}"))
    except Exception:
        return False


def _mark_discovery_backoff(account_id: int, exc: Exception) -> None:
    """账号级 Reddit 429 后写入退避标记：本轮及下一 tick 的发现直接跳过，防雪崩。"""
    from app.utils.rate_limiter import get_redis

    try:
        get_redis().set(
            f"backoff:reddit:discovery:{account_id}", "1", ex=DISCOVERY_BACKOFF_SECONDS
        )
    except Exception:
        logger.debug("discovery backoff write failed", exc_info=True)
    logger.warning(
        "Reddit discovery backoff %ss for account #%s: %s",
        DISCOVERY_BACKOFF_SECONDS,
        account_id,
        str(exc)[:200],
    )


# ============ 发布 ETA 直投 ============
def _try_direct_dispatch(db: Session, kind: str, item_id: int, run_at: datetime) -> None:
    """到点直投发布任务（beat 15 分钟扫描降级为兜底）；失败静默由扫描拾取。"""
    try:
        from app.tasks.reddit_tasks import (
            publish_reddit_comment_by_id,
            publish_reddit_post_by_id,
            schedule_publish,
        )

        task = publish_reddit_post_by_id if kind == "post" else publish_reddit_comment_by_id
        schedule_publish(db, kind, item_id, task, run_at)
    except Exception:  # noqa: BLE001 — broker 不可用时降级为发布扫描拾取
        logger.debug("Direct publish dispatch failed kind=%s id=%s", kind, item_id, exc_info=True)


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


# ============ 发帖动作（阶段 2：骨架规划，AI 生成出环）============
def _create_campaign_post(
    db: Session,
    campaign: RedditCampaign,
    account: SocialAccount,
    planned_at: datetime,
) -> RedditCampaignAction | None:
    """落一条帖子骨架动作（纯 DB，无 AI/外部调用）：dedup 占位 + 排期 + 生成队列标记。

    生成上下文（社区/类型/角度/风控）由 generation 队列任务在执行时重新推导；
    dedup_key 维持按天语义：post:{campaign}:{account}:{yyyymmdd}，每天每账号一条。
    dedup 冲突（同 campaign+账号+计划日已规划）返回 None。
    """
    dedup_key = f"post:{campaign.id}:{account.id}:{planned_at:%Y%m%d}"
    action = RedditCampaignAction(
        campaign_id=campaign.id,
        site_id=campaign.site_id,
        account_id=account.id,
        action_type="post",
        status="pending",
        planned_at=planned_at,
        dedup_key=dedup_key,
        generation_status="queued",
        config_version=campaign.config_version,
    )
    db.add(action)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        # dedup 冲突不视为故障，但必须校验已存在动作的业务意图一致
        #（同 campaign / 账号 / 计划日）；不匹配的冲突按错误暴露，不静默视为成功。
        existing = (
            db.query(RedditCampaignAction)
            .filter(
                RedditCampaignAction.campaign_id == campaign.id,
                RedditCampaignAction.dedup_key == dedup_key,
            )
            .first()
        )
        if existing is not None and (
            existing.account_id != account.id
            or existing.planned_at is None
            or as_bj_naive(existing.planned_at).date() != as_bj_naive(planned_at).date()
        ):
            logger.error(
                "Campaign dedup conflict with mismatched intent: campaign=%s dedup=%s "
                "existing(account=%s, planned_at=%s) vs new(account=%s, planned_at=%s)",
                campaign.id,
                dedup_key,
                existing.account_id,
                existing.planned_at,
                account.id,
                planned_at,
            )
        return None
    return action


# ============ 评论动作 ============
def _comment_candidates(
    db: Session,
    campaign: RedditCampaign,
    account: SocialAccount,
    limit: int = 4,
    *,
    now: datetime | None = None,
    apply_cooldown: bool = True,
    subs: list[str] | None = None,
) -> list[dict]:
    """自动发现评论目标：campaign 社区/人设社区中未互动过的讨论帖。"""
    from app.services.reddit_community_rotation import order_subreddits

    if _discovery_paused(account.id):
        return []  # 账号级 429 退避中：跳过发现，等下个 tick
    # 调用方（_plan_comments）已算过目标社区时传入复用，省去重复查询
    if subs is None:
        subs = _target_subreddits(db, campaign, account)
    if not subs:
        return []
    ordered = order_subreddits(
        db,
        account_id=account.id,
        candidates=subs,
        now=now or now_bj(),
        limit=3,
        apply_cooldown=apply_cooldown,
    )
    terms: list[str] = []
    if campaign.product_id:
        product = db.get(RedditProduct, campaign.product_id)
        if product:
            from app.services.reddit_discover import product_search_terms

            terms = product_search_terms(keywords=[r.keyword for r in (product.keyword_rows or [])])

    client = get_reddit_client_for_account(account)
    candidates: list[dict] = []
    seen_ids: set[str] = set()
    for sub in ordered:
        try:
            if terms:
                keyword = random.choice(terms)
                items = client.search_posts(sub, keyword, limit=8)
            else:
                keyword = ""
                items = client.list_feed(sub, limit=8)
        except RedditApiError as exc:
            # 账号级 429：整轮发现必失败，写退避并停止遍历后续社区
            _mark_discovery_backoff(account.id, exc)
            break
        batch: list[dict] = []
        for item in items:
            thing_id = str(item.get("thing_id") or "")
            url = item.get("url") or ""
            if not thing_id or not url or thing_id in seen_ids:
                continue
            seen_ids.add(thing_id)
            batch.append(
                {
                    "url": url,
                    "thing_id": thing_id,
                    "title": item.get("title") or "",
                    "body": item.get("body") or "",
                    "subreddit": item.get("subreddit") or sub,
                    "keyword": keyword,
                }
            )
        # 按候选批量反查该账号已评论/已计划评论的目标，替代全量历史加载
        seen_things = _seen_comment_things(
            db, campaign.site_id, account.id, [b["thing_id"] for b in batch]
        )
        for cand in batch:
            if cand["thing_id"] in seen_things:
                continue
            candidates.append(cand)
            if len(candidates) >= limit:
                return candidates
    return candidates


def _seen_comment_things(
    db: Session, site_id: int, account_id: int, thing_ids: list[str]
) -> set[str]:
    """候选中该账号已评论或已计划评论的 thing_id（按候选 IN 反查，避免全量历史加载）。"""
    if not thing_ids:
        return set()
    seen = {
        row[0]
        for row in db.query(RedditComment.target_thing_id)
        .filter(
            RedditComment.account_id == account_id,
            RedditComment.site_id == site_id,
            RedditComment.target_thing_id.in_(thing_ids),
        )
        .all()
    }
    seen |= {
        row[0]
        for row in db.query(RedditCampaignAction.target_thing_id)
        .filter(
            RedditCampaignAction.site_id == site_id,
            RedditCampaignAction.account_id == account_id,
            RedditCampaignAction.action_type == "comment",
            RedditCampaignAction.target_thing_id.in_(thing_ids),
        )
        .all()
    }
    return seen


def _candidate_cache_key(campaign_id: int, account_id: int) -> str:
    return f"discovery:campaign:{campaign_id}:comments:{account_id}"


def _create_campaign_comment(
    db: Session,
    campaign: RedditCampaign,
    account: SocialAccount,
    planned_at: datetime,
    candidate: dict,
) -> RedditCampaignAction | None:
    """落一条评论骨架动作（纯 DB）：候选快照随行持久化（generation_context），
    AI 生成出环到 generation 队列。候选正文随骨架落库，生成任务免重复抓取。
    dedup 冲突（该账号已评论/已计划同一目标帖）返回 None。
    """
    thing_id = str(candidate.get("thing_id") or "")
    dedup_key = f"comment:{campaign.id}:{account.id}:{thing_id}"
    context = {
        "url": candidate.get("url") or "",
        "thing_id": thing_id,
        "title": candidate.get("title") or "",
        "body": candidate.get("body") or "",
        "subreddit": candidate.get("subreddit") or "",
        "keyword": candidate.get("keyword") or "",
    }
    action = RedditCampaignAction(
        campaign_id=campaign.id,
        site_id=campaign.site_id,
        account_id=account.id,
        action_type="comment",
        status="pending",
        target_thing_id=thing_id,
        target_post_url=candidate.get("url") or "",
        planned_at=planned_at,
        dedup_key=dedup_key,
        generation_status="queued",
        generation_context=json.dumps(context, ensure_ascii=False),
        config_version=campaign.config_version,
    )
    db.add(action)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        existing = (
            db.query(RedditCampaignAction)
            .filter(
                RedditCampaignAction.campaign_id == campaign.id,
                RedditCampaignAction.dedup_key == dedup_key,
            )
            .first()
        )
        if existing is not None and (
            existing.account_id != account.id
            or existing.target_thing_id != thing_id
        ):
            logger.error(
                "Campaign comment dedup conflict with mismatched intent: campaign=%s "
                "dedup=%s existing(account=%s, thing=%s) vs new(account=%s, thing=%s)",
                campaign.id,
                dedup_key,
                existing.account_id,
                existing.target_thing_id,
                account.id,
                thing_id,
            )
        return None
    return action


# ============ 评论候选发现出环（阶段 2）============
def _pop_comment_candidate(db: Session, campaign: RedditCampaign, account: SocialAccount) -> dict | None:
    """从 Redis 发现缓存弹出一个未互动候选（纯 DB + Redis，无 Reddit 调用）。

    缓存空/耗尽时异步入队发现任务（账号级去抖），本轮评论排期跳过——
    发现调用（search/list）不再占用 tick 窗口，由 serp 队列异步补池。
    """
    from app.utils.rate_limiter import get_redis

    try:
        r = get_redis()
    except Exception:  # noqa: BLE001 — Redis 不可用：本轮跳过，下轮重试
        logger.debug("candidate cache read failed", exc_info=True)
        return None
    key = _candidate_cache_key(campaign.id, account.id)
    for _ in range(8):  # 单轮最多弹 8 个，弹完已互动的不再回填
        raw = r.lpop(key)
        if raw is None:
            break
        try:
            cand = json.loads(raw if isinstance(raw, str) else raw.decode())
        except Exception:  # noqa: BLE001 — 坏行直接丢弃
            continue
        thing_id = str(cand.get("thing_id") or "")
        url = cand.get("url") or ""
        if not thing_id or not url:
            continue
        if thing_id in _seen_comment_things(db, campaign.site_id, account.id, [thing_id]):
            continue
        return {
            "url": url,
            "thing_id": thing_id,
            "title": cand.get("title") or "",
            "body": cand.get("body") or "",
            "subreddit": cand.get("subreddit") or "",
            "keyword": cand.get("keyword") or "",
        }
    _enqueue_comment_discovery(campaign.id, account.id)
    return None


def _enqueue_comment_discovery(campaign_id: int, account_id: int) -> bool:
    """异步入队候选发现任务（Redis 锁去抖）；broker 不可用静默降级（下轮 tick 再试）。"""
    from app.tasks.reddit_campaign_tasks import refresh_campaign_comment_candidates
    from app.utils.rate_limiter import get_redis

    try:
        r = get_redis()
        key = f"trigger:reddit:campaign_discovery:{campaign_id}:{account_id}"
        if not r.set(key, "1", nx=True, ex=CAMPAIGN_DISCOVERY_MIN_INTERVAL_SECONDS):
            return False
        refresh_campaign_comment_candidates.apply_async(
            args=[campaign_id, account_id],
            task_id=f"reddit-campaign-discovery-{campaign_id}-{account_id}",
        )
        return True
    except Exception:  # noqa: BLE001
        logger.debug("campaign comment discovery enqueue failed", exc_info=True)
        return False


def run_comment_discovery(db: Session, campaign_id: int, account_id: int) -> dict:
    """发现候选并写 Redis 缓存（serp 队列任务调用；账号级最小间隔去抖）。

    tick 只消费缓存不再内联 Reddit 发现调用：发现耗时与 429 抖动出环后，
    账号级退避（_discovery_paused）与最小间隔双保险防雪崩。
    """
    from app.utils.rate_limiter import get_redis

    try:
        r = get_redis()
        debounce_key = f"discovery:campaign:interval:{campaign_id}:{account_id}"
        if not r.set(
            debounce_key, "1", nx=True, ex=CAMPAIGN_DISCOVERY_MIN_INTERVAL_SECONDS
        ):
            return {"queued": 0, "reason": "debounced"}
    except Exception:  # noqa: BLE001
        logger.debug("discovery debounce check failed", exc_info=True)
        return {"queued": 0, "reason": "redis_unavailable"}

    campaign = db.get(RedditCampaign, campaign_id)
    account = db.get(SocialAccount, account_id)
    if campaign is None or account is None or campaign.status != "running":
        return {"queued": 0, "reason": "inactive"}
    if _discovery_paused(account_id):
        return {"queued": 0, "reason": "backoff"}
    candidates = _comment_candidates(db, campaign, account)
    if not candidates:
        return {"queued": 0}
    try:
        pipe = r.pipeline()
        key = _candidate_cache_key(campaign_id, account_id)
        pipe.delete(key)
        pipe.rpush(key, *[json.dumps(c, ensure_ascii=False) for c in candidates])
        pipe.expire(key, CANDIDATE_CACHE_TTL_SECONDS)
        pipe.execute()
    except Exception:  # noqa: BLE001 — 缓存写失败不致命：下轮 tick 重新发现
        logger.debug("candidate cache write failed", exc_info=True)
        return {"queued": 0, "reason": "cache_write_failed"}
    logger.info(
        "Campaign comment discovery cached: campaign=%s account=%s candidates=%d",
        campaign_id,
        account_id,
        len(candidates),
    )
    return {"queued": len(candidates)}


# ============ 点赞动作 ============
def _voted_things_among(db: Session, site_id: int, thing_ids: list[str]) -> set[str]:
    """候选中已被本站任一账号投过赞的 thing_id（按候选 IN 反查，避免全站集合加载）。"""
    if not thing_ids:
        return set()
    return {
        row[0]
        for row in db.query(RedditVoteLog.thing_id)
        .filter(
            RedditVoteLog.site_id == site_id,
            RedditVoteLog.direction == 1,
            RedditVoteLog.thing_id.in_(thing_ids),
        )
        .all()
    }


def _actioned_things_among(db: Session, site_id: int, thing_ids: list[str]) -> set[str]:
    """候选中已有本站点赞计划/动作的 thing_id（按候选 IN 反查）。"""
    if not thing_ids:
        return set()
    return {
        row[0]
        for row in db.query(RedditCampaignAction.target_thing_id)
        .filter(
            RedditCampaignAction.site_id == site_id,
            RedditCampaignAction.action_type.in_(_UPVOTE_TYPES),
            RedditCampaignAction.target_thing_id.in_(thing_ids),
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
    from app.services.reddit_community_rotation import order_subreddits

    if remaining <= 0 or not pool:
        return 0
    searcher = random.choice(pool)
    if _discovery_paused(searcher.id):
        return 0  # 账号级 429 退避中：跳过发现，等下个 tick
    client = get_reddit_client_for_account(searcher)
    subs = _target_subreddits(db, campaign, searcher)
    if not subs:
        return 0
    ordered_subs = order_subreddits(
        db, account_id=searcher.id, candidates=subs, now=now, limit=3
    )

    terms: list[str] = []
    if campaign.product_id:
        product = db.get(RedditProduct, campaign.product_id)
        if product:
            from app.services.reddit_discover import product_search_terms

            terms = product_search_terms(keywords=[r.keyword for r in (product.keyword_rows or [])])

    excluded: set[str] = set()
    pool_names = {(a.account_name or "").lower() for a in pool}
    cutoff = now_bj() - timedelta(days=UPVOTE_RELATED_MAX_AGE_DAYS)
    candidates: list[dict] = []
    seen_ids: set[str] = set()
    for sub in ordered_subs:
        try:
            if terms:
                keyword = random.choice(terms)
                items = client.search_posts(sub, keyword, limit=10)
            else:
                keyword = ""
                items = client.list_feed(sub, limit=10)
        except RedditApiError as exc:
            # 账号级 429：整轮发现必失败，写退避并停止遍历后续社区
            _mark_discovery_backoff(searcher.id, exc)
            break
        batch: list[dict] = []
        for item in items:
            thing_id = str(item.get("thing_id") or "")
            url = item.get("url") or ""
            if not thing_id or not url or thing_id in seen_ids:
                continue
            seen_ids.add(thing_id)
            author = (item.get("author") or "").lower()
            if author and author in pool_names:
                continue  # 池内互赞自己人帖子无意义
            score = int(item.get("score") or 0)
            if not (UPVOTE_RELATED_SCORE_MIN <= score <= UPVOTE_RELATED_SCORE_MAX):
                continue
            created_utc = int(item.get("created_utc") or 0)
            if created_utc and from_epoch_bj(created_utc) < cutoff:
                continue
            batch.append({"thing_id": thing_id, "url": url})
        # 按候选批量反查已投票/已排期目标，替代全站集合加载（查询量 O(候选) 不随历史增长）
        excluded = _voted_things_among(
            db, campaign.site_id, [b["thing_id"] for b in batch]
        ) | _actioned_things_among(db, campaign.site_id, [b["thing_id"] for b in batch])
        for cand in batch:
            if cand["thing_id"] in excluded:
                continue
            candidates.append(cand)
            if len(candidates) >= remaining * 2:
                break
        if len(candidates) >= remaining * 2:
            break

    planned = 0
    for cand in candidates:
        if planned >= remaining or planned >= MAX_UPVOTE_PLANS_PER_TICK:
            break
        voter = random.choice(pool)
        planned_at = _plan_time(db, voter.id, now, hour_start, hour_end)
        if planned_at.date() != now.date():
            continue  # 该账号今天排不进了：换下一个候选，不顺延到明天
        action = _insert_action(
            db,
            campaign_id=campaign.id,
            site_id=campaign.site_id,
            account_id=voter.id,
            action_type="upvote_related",
            status="pending",
            target_thing_id=cand["thing_id"],
            target_post_url=cand["url"],
            planned_at=planned_at,
            dedup_key=f"upvote:{voter.id}:{cand['thing_id']}",
            config_version=campaign.config_version,
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
    """自己帖子点赞（提热度）：池内其他账号投票（voter != author，规避 Reddit 同号自赞判定）。

    self_upvote_delay_minutes 是自赞**截止窗口**而非定时点：只补赞发布已满
    SELF_UPVOTE_MIN_AGE_MINUTES 分钟、且尚未超过 delay 分钟的帖子；超过
    delay 的旧帖不再回头补赞（对旧帖突然投票本身也是异常特征）。
    """
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

    if not posts:
        return 0
    # 候选批量反查（查询量 O(候选)，不随 campaign 历史增长）：
    # 1) 已计划的自赞 dedup_key 只按"可能写入的键" IN 反查，替代该 campaign 全量加载
    possible_keys = [
        f"upvote_self:{voter.id}:{post.id}"
        for post in posts
        for voter in pool
        if voter.id != post.account_id
    ]
    existing_dedups: set[str] = set()
    if possible_keys:
        existing_dedups = {
            row[0]
            for row in db.query(RedditCampaignAction.dedup_key)
            .filter(
                RedditCampaignAction.campaign_id == campaign.id,
                RedditCampaignAction.dedup_key.in_(possible_keys),
            )
            .all()
        }
    # 2) 已投过赞的 (账号, thing) 对：一次批量查询，替代循环内逐对 N+1
    already_pairs = {
        (row[0], row[1])
        for row in db.query(RedditVoteLog.account_id, RedditVoteLog.thing_id)
        .filter(
            RedditVoteLog.account_id.in_([a.id for a in pool]),
            RedditVoteLog.thing_id.in_([p.reddit_post_id for p in posts]),
            RedditVoteLog.direction == 1,
        )
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
            if (voter.id, post.reddit_post_id) in already_pairs:
                continue
            planned_at = _plan_time(db, voter.id, now, hour_start, hour_end)
            if planned_at.date() != now.date():
                continue  # 该账号今天排不进了：换下一个投票账号
            action = _insert_action(
                db,
                campaign_id=campaign.id,
                site_id=campaign.site_id,
                account_id=voter.id,
                action_type="upvote_self",
                status="pending",
                target_thing_id=post.reddit_post_id,
                target_post_url=post.reddit_permalink,
                planned_at=planned_at,
                dedup_key=dedup_key,
                config_version=campaign.config_version,
            )
            if action:
                existing_dedups.add(dedup_key)
                planned += 1
                break  # 每帖每 tick 只安排一个赞
    return planned


# ============ 对账与执行 ============
def _finish_action(db: Session, action: RedditCampaignAction, status: str, *, error_message: str | None = None, skip_reason: str | None = None) -> None:
    """非租约路径（pending reconcile）的状态迁移守卫：条件 UPDATE，不用 ORM 读-改-写。"""
    now = now_bj()
    db.execute(
        text(
            """
            UPDATE reddit_campaign_actions
            SET status = :status,
                executed_at = COALESCE(executed_at, :now),
                error_message = :err,
                skip_reason = :skip,
                updated_at = :now
            WHERE id = :id AND status = :expected
            """
        ),
        {
            "status": status,
            "err": (error_message[:500] if error_message else None),
            "skip": (skip_reason[:200] if skip_reason else None),
            "now": now,
            "id": action.id,
            "expected": action.status,
        },
    )
    db.commit()


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


def reconcile_uncertain_actions(db: Session, limit: int = 50) -> dict:
    """reconciling 对账分流（结果未知动作的核实，禁止自动重试直至结论落定）。

    对账能力分型：
    - post / comment：以已持久化的内容状态（posted 事实 = thing_id 已回填）核实；
    - upvote：平台无本账号投票状态权威查询 → 按 attempt 请求发出记录三分类：
      确认未发出 → confirmed_absent（预算内回 pending 重试）；已发出且结果未知 →
      needs_review 人工处理，禁止凭目标帖存在推断投票成功。
    """
    changed = {"done": 0, "failed": 0, "skipped": 0, "needs_review": 0, "pending": 0, "waiting": 0}
    rows = (
        db.query(RedditCampaignAction)
        .filter(RedditCampaignAction.status == "reconciling")
        .order_by(RedditCampaignAction.id.asc())
        .limit(limit)
        .all()
    )
    now = now_bj()
    for action in rows:
        gen = action.execution_lease_generation
        attempt = (
            db.execute(
                text(
                    """
                    SELECT request_started_at FROM reddit_campaign_action_attempts
                    WHERE action_id = :id AND phase = 'execution' AND attempt_no = :gen
                    """
                ),
                {"id": action.id, "gen": gen},
            )
            .first()
        )
        request_started = attempt[0] if attempt else None
        if action.action_type in _CONTENT_TYPES:
            content = (
                db.get(RedditPost, action.post_id)
                if action.post_id
                else db.get(RedditComment, action.comment_id) if action.comment_id else None
            )
            if content is not None and content.status == "posted":
                _finish_reconciling(
                    db, action.id, gen, "done", result_status="confirmed_success"
                )
                changed["done"] += 1
            elif content is not None and content.status == "failed":
                # 发布任务明确失败 = 副作用未发生
                _finish_reconciling(
                    db,
                    action.id,
                    gen,
                    "failed",
                    result_status="confirmed_absent",
                    error_message=(content.error_message or "发布失败")[:500],
                )
                changed["failed"] += 1
            elif content is not None and content.status == "rejected":
                _finish_reconciling(
                    db, action.id, gen, "skipped", skip_reason="content_rejected"
                )
                changed["skipped"] += 1
            else:
                # 内容仍在发布链路（scheduled/approved/pending_review）：等待，保持 reconciling
                changed["waiting"] += 1
        else:  # upvote：无权威查询，按请求发出记录分流
            if request_started is None:
                # 请求确认未发出 → confirmed_absent；预算内回 pending 自动重试
                if action.execution_attempt_count <= action.execution_max_attempts:
                    db.execute(
                        text(
                            """
                            UPDATE reddit_campaign_actions
                            SET status = 'pending', planned_at = :now, available_at = :now,
                                result_status = 'confirmed_absent', last_error_code = 'confirmed_not_sent',
                                lease_until = NULL, lease_owner = NULL, updated_at = :now
                            WHERE id = :id AND status = 'reconciling'
                              AND execution_lease_generation = :gen
                            """
                        ),
                        {"now": now, "id": action.id, "gen": gen},
                    )
                    _respond_attempt(db, action.id, "execution", gen, error_code="confirmed_not_sent")
                    changed["pending"] += 1
                else:
                    _finish_reconciling(
                        db,
                        action.id,
                        gen,
                        "failed",
                        result_status="confirmed_absent",
                        error_message="重试预算耗尽且请求未发出",
                        error_code="confirmed_not_sent",
                    )
                    changed["failed"] += 1
            else:
                # 请求已发出、结果未知：转人工，绝不凭目标帖存在推断投票成功
                _finish_reconciling(
                    db,
                    action.id,
                    gen,
                    "needs_review",
                    result_status="unknown",
                    error_message="投票结果未知（请求已发出，无权威查询接口），需人工核实",
                    error_code="vote_outcome_unknown",
                )
                changed["needs_review"] += 1
    db.commit()
    return changed


def _finish_reconciling(
    db: Session,
    action_id: int,
    generation: int,
    status: str,
    *,
    result_status: str | None = None,
    error_message: str | None = None,
    error_code: str | None = None,
    skip_reason: str | None = None,
) -> None:
    """对账结论落定：条件 UPDATE（reconciling → 终态），同步回写 attempt。"""
    now = now_bj()
    db.execute(
        text(
            """
            UPDATE reddit_campaign_actions
            SET status = :status, executed_at = COALESCE(executed_at, :now),
                error_message = :err, skip_reason = :skip,
                result_status = :rs, last_error_code = :ec,
                lease_until = NULL, lease_owner = NULL, updated_at = :now
            WHERE id = :id AND status = 'reconciling' AND execution_lease_generation = :gen
            """
        ),
        {
            "status": status,
            "err": (error_message[:500] if error_message else None),
            "skip": (skip_reason[:200] if skip_reason else None),
            "rs": result_status,
            "ec": error_code,
            "now": now,
            "id": action_id,
            "gen": generation,
        },
    )
    _respond_attempt(db, action_id, "execution", generation, error_code=error_code, error_message=error_message)


def _respond_attempt(
    db: Session,
    action_id: int,
    phase: str,
    attempt_no: int,
    *,
    error_code: str | None = None,
    error_message: str | None = None,
    external_object_id: str | None = None,
    result_summary: dict | None = None,
) -> None:
    """attempt 审计记录回写（responded 段；与状态迁移同事务提交）。"""
    import json as _json

    db.execute(
        text(
            """
            UPDATE reddit_campaign_action_attempts
            SET request_state = 'responded', responded_at = :now,
                error_code = :ec, error_message = :em,
                external_object_id = COALESCE(:ext, external_object_id),
                result_summary = COALESCE(CAST(:summary AS json), result_summary),
                updated_at = :now
            WHERE action_id = :id AND phase = :phase AND attempt_no = :no
              AND request_state = 'request_pending'
            """
        ),
        {
            "now": now_bj(),
            "ec": error_code,
            "em": (error_message[:500] if error_message else None),
            "ext": external_object_id,
            "summary": (_json.dumps(result_summary, ensure_ascii=False) if result_summary else None),
            "id": action_id,
            "phase": phase,
            "no": attempt_no,
        },
    )


def recover_stale_actions(db: Session, timeout_minutes: int = STALE_EXECUTING_MINUTES) -> dict:
    """回收执行租约过期的动作（按 lease_until 判定；无租约的遗留行按 updated_at 兜底）。

    分流依据 attempts 两段持久化：
    - 预写尝试记录且 request_started_at 为空 → 请求确认未发出 → failed（confirmed_absent，可重试）
    - 请求已发出且响应未回写 / 无尝试记录的遗留行 → 结果未知 → reconciling（禁止自动重试）
    """
    now = now_bj()
    cutoff = now - timedelta(minutes=timeout_minutes)
    rows = db.execute(
        text(
            """
            SELECT a.id, a.execution_lease_generation,
                   t.request_started_at, t.request_state
            FROM reddit_campaign_actions a
            LEFT JOIN reddit_campaign_action_attempts t
                   ON t.action_id = a.id AND t.phase = 'execution'
                  AND t.attempt_no = a.execution_lease_generation
            WHERE a.status = 'executing'
              AND (
                    (a.lease_until IS NOT NULL AND a.lease_until <= :now)
                 OR (a.lease_until IS NULL AND a.updated_at <= :cutoff)
                  )
            """
        ),
        {"now": now, "cutoff": cutoff},
    ).all()
    counts = {"failed": 0, "reconciling": 0}
    for action_id, gen, request_started_at, request_state in rows:
        request_known_unsent = request_state == "request_pending" and request_started_at is None
        if request_known_unsent:
            db.execute(
                text(
                    """
                    UPDATE reddit_campaign_actions
                    SET status = 'failed', executed_at = :now,
                        error_message = '执行租约过期且请求未发出', result_status = 'confirmed_absent',
                        last_error_code = 'lease_expired_not_sent',
                        lease_until = NULL, lease_owner = NULL, updated_at = :now
                    WHERE id = :id AND status = 'executing' AND execution_lease_generation = :gen
                    """
                ),
                {"now": now, "id": action_id, "gen": gen},
            )
            _respond_attempt(db, action_id, "execution", gen, error_code="lease_expired_not_sent")
            counts["failed"] += 1
        else:
            # 请求可能已发出：外部副作用未知 → reconciling，由对账分流核实
            db.execute(
                text(
                    """
                    UPDATE reddit_campaign_actions
                    SET status = 'reconciling', executed_at = COALESCE(executed_at, :now),
                        error_message = '执行租约过期且请求结果未知，待对账', result_status = 'unknown',
                        last_error_code = 'lease_expired_unknown',
                        lease_until = NULL, lease_owner = NULL, updated_at = :now
                    WHERE id = :id AND status = 'executing' AND execution_lease_generation = :gen
                    """
                ),
                {"now": now, "id": action_id, "gen": gen},
            )
            counts["reconciling"] += 1
    if counts["failed"] or counts["reconciling"]:
        db.commit()
    return counts


def due_upvote_action_ids(db: Session, limit: int = 30) -> list[int]:
    """到期待执行的点赞动作 ID（仅 running campaign；暂停/结束的不再发放）。"""
    now = now_bj()
    rows = (
        db.query(RedditCampaignAction.id)
        .join(RedditCampaign, RedditCampaignAction.campaign_id == RedditCampaign.id)
        .filter(
            RedditCampaignAction.status == "pending",
            RedditCampaignAction.action_type.in_(_UPVOTE_TYPES),
            RedditCampaignAction.planned_at.isnot(None),
            RedditCampaignAction.planned_at <= now,
            (RedditCampaignAction.available_at.is_(None)) | (RedditCampaignAction.available_at <= now),
            RedditCampaign.status == "running",
        )
        .order_by(RedditCampaignAction.planned_at.asc())
        .limit(limit)
        .all()
    )
    return [r[0] for r in rows]


def _worker_id() -> str:
    """worker 身份标识（观测用；终态校验以 fencing 代次为准）。"""
    return f"{socket.gethostname()}:{os.getpid()}"[:64]


def get_action_lease_generation(db: Session, action_id: int) -> int | None:
    """读取动作当前执行租约代次（软超时处理器定界用；动作不存在返回 None）。"""
    row = db.execute(
        text(
            "SELECT execution_lease_generation FROM reddit_campaign_actions WHERE id = :id"
        ),
        {"id": action_id},
    ).first()
    return int(row[0]) if row else None


def _claim_execute(
    db: Session, action_id: int, worker_id: str, now: datetime, lease_minutes: int
) -> int | None:
    """原子领取核心：pending → executing，单事务完成 fencing 代次递增 + 租约 +
    attempts 预写（request_pending）+ 尝试计数。返回新代次；不可领取返回 None。

    attempts 预写与领取同事务提交：worker 在领取后、请求发出前崩溃时，恢复程序
    依据该记录判定"请求未发出"，而非凭时间推断盲目重试。
    """
    row = db.execute(
        text(
            """
            UPDATE reddit_campaign_actions
            SET status = 'executing',
                execution_lease_generation = execution_lease_generation + 1,
                lease_owner = :wid,
                lease_until = :lease_until,
                updated_at = :now
            WHERE id = :id AND status = 'pending'
            RETURNING execution_lease_generation
            """
        ),
        {
            "wid": worker_id,
            "lease_until": now + timedelta(minutes=lease_minutes),
            "now": now,
            "id": action_id,
        },
    ).first()
    if row is None:
        return None
    generation = int(row[0])
    db.execute(
        text(
            """
            INSERT INTO reddit_campaign_action_attempts
                (action_id, phase, attempt_no, request_state, worker_id, created_at, updated_at)
            VALUES (:id, 'execution', :gen, 'request_pending', :wid, :now, :now)
            """
        ),
        {"id": action_id, "gen": generation, "wid": worker_id, "now": now},
    )
    db.execute(
        text(
            """
            UPDATE reddit_campaign_actions
            SET execution_attempt_count = :gen, updated_at = :now
            WHERE id = :id
            """
        ),
        {"gen": generation, "now": now, "id": action_id},
    )
    return generation


def claim_action(
    db: Session,
    action_id: int,
    *,
    worker_id: str | None = None,
    lease_minutes: int = EXECUTE_LEASE_MINUTES,
) -> int | None:
    """单条原子领取（保持接口）：成功返回本次执行租约代次，失败返回 None。"""
    wid = (worker_id or _worker_id())[:64]
    now = now_bj()
    generation = _claim_execute(db, action_id, wid, now, lease_minutes)
    if generation is None:
        db.rollback()
        return None
    db.commit()
    return generation


def claim_due_actions_batch(
    db: Session, limit: int = 10, *, lease_minutes: int = EXECUTE_LEASE_MINUTES
) -> list[dict]:
    """批量原子领取（FOR UPDATE SKIP LOCKED）：并发拉取式 worker 安全取活。

    返回 [{"action_id", "generation"}]；行级锁冲突跳过，不阻塞其他 worker。
    """
    wid = _worker_id()
    now = now_bj()
    rows = db.execute(
        text(
            """
            SELECT id FROM reddit_campaign_actions
            WHERE status = 'pending'
              AND action_type IN ('upvote_related', 'upvote_self')
              AND planned_at IS NOT NULL AND planned_at <= :now
              AND (available_at IS NULL OR available_at <= :now)
            ORDER BY planned_at ASC
            LIMIT :limit
            FOR UPDATE SKIP LOCKED
            """
        ),
        {"now": now, "limit": limit},
    ).all()
    claimed: list[dict] = []
    for (action_id,) in rows:
        generation = _claim_execute(db, action_id, wid, now, lease_minutes)
        if generation is not None:
            claimed.append({"action_id": action_id, "generation": generation})
    db.commit()
    return claimed


# 限流/熔断自动重试上限（attempt 0-based：0/1 重试，2 耗尽标 failed）
_ACTION_AUTO_RETRIES = 2

# 明确的平台拒绝/限流响应（结果已知：副作用未发生）→ 可重试
_RETRYABLE_STATUS = (429, 503)


def _finish_executing(
    db: Session,
    action_id: int,
    generation: int,
    status: str,
    *,
    error_message: str | None = None,
    skip_reason: str | None = None,
    result_status: str | None = None,
    error_code: str | None = None,
    external_object_id: str | None = None,
    result_summary: dict | None = None,
) -> bool:
    """执行租约下的终态回写守卫（fencing）：条件 UPDATE 校验执行代次。

    终态与 attempts 回写（responded 段）在同一数据库事务提交；旧 worker 迟到
    返回时代次不匹配 → 返回 False，其外部结果不得直接写状态（只能进对账）。
    """
    now = now_bj()
    updated = db.execute(
        text(
            """
            UPDATE reddit_campaign_actions
            SET status = :status, executed_at = COALESCE(executed_at, :now),
                error_message = :err, skip_reason = :skip,
                result_status = :rs, last_error_code = :ec,
                lease_until = NULL, lease_owner = NULL, updated_at = :now
            WHERE id = :id AND execution_lease_generation = :gen
              AND status IN ('executing', 'reconciling')
            """
        ),
        {
            "status": status,
            "err": (error_message[:500] if error_message else None),
            "skip": (skip_reason[:200] if skip_reason else None),
            "rs": result_status,
            "ec": error_code,
            "now": now,
            "id": action_id,
            "gen": generation,
        },
    ).rowcount
    if updated != 1:
        db.rollback()
        logger.warning(
            "Fencing rejected terminal write: action=%s gen=%s target=%s", action_id, generation, status
        )
        return False
    _respond_attempt(
        db,
        action_id,
        "execution",
        generation,
        error_code=error_code,
        error_message=error_message,
        external_object_id=external_object_id,
        result_summary=result_summary,
    )
    db.commit()
    return True


def _release_unstarted(db: Session, action_id: int, generation: int, code: str) -> None:
    """暂停/结束协议：已领取但请求未发出 → 停止执行，回 pending 等恢复重取。

    暂停不得把在途动作伪装成成功或失败；已发出的请求走 reconciling 对账。
    """
    now = now_bj()
    db.execute(
        text(
            """
            UPDATE reddit_campaign_actions
            SET status = 'pending', lease_until = NULL, lease_owner = NULL,
                result_status = 'confirmed_absent', last_error_code = :code, updated_at = :now
            WHERE id = :id AND execution_lease_generation = :gen AND status = 'executing'
            """
        ),
        {"code": code, "now": now, "id": action_id, "gen": generation},
    )
    _respond_attempt(db, action_id, "execution", generation, error_code=code)
    db.commit()


def _mark_request_started(db: Session, action_id: int, generation: int) -> bool:
    """外部请求即将发出：持久化 request_started_at（独立短事务，先于外部调用提交）。

    此记录是"请求可能已发出"与"副作用已发生"两个时刻的判定依据：worker 在
    请求发出后、响应回写前被强杀时，恢复程序据此进入 reconciling 而非盲目重试。
    """
    updated = db.execute(
        text(
            """
            UPDATE reddit_campaign_action_attempts AS t
            SET request_started_at = :now, updated_at = :now
            FROM reddit_campaign_actions AS a
            WHERE t.action_id = a.id AND t.action_id = :id
              AND t.phase = 'execution' AND t.attempt_no = :gen
              AND a.execution_lease_generation = :gen
            """
        ),
        {"now": now_bj(), "id": action_id, "gen": generation},
    ).rowcount
    db.commit()
    return updated == 1


def mark_action_reconciling(db: Session, action_id: int, generation: int | None = None, message: str | None = None) -> bool:
    """结果未知分流：executing → reconciling（软超时/请求已发出且响应未知时调用）。

    供任务层软超时处理器与迟到 worker 使用：不承诺结果，交由对账核实。
    """
    gen_clause = "AND execution_lease_generation = :gen" if generation is not None else "AND TRUE"
    params: dict = {
        "now": now_bj(),
        "id": action_id,
        "msg": (message or "外部请求结果未知，待对账")[:500],
    }
    if generation is not None:
        params["gen"] = generation
    updated = db.execute(
        text(
            f"""
            UPDATE reddit_campaign_actions
            SET status = 'reconciling', result_status = 'unknown',
                last_error_code = 'request_unknown', error_message = :msg,
                lease_until = NULL, lease_owner = NULL, updated_at = :now
            WHERE id = :id AND status = 'executing' {gen_clause}
            """
        ),
        params,
    ).rowcount
    db.commit()
    return updated == 1


def revalidate_action_config(db: Session, action: RedditCampaignAction, campaign: RedditCampaign) -> tuple[bool, str | None]:
    """config_version 影响分级（阶段 3）：动作快照过期时判定能否继续执行。

    - 账号已移出 account_ids → 不再允许执行（config_account_removed）
    - post 目标社区已移出 subreddits → 执行目标失效（config_subreddit_removed）
    - 其余变更（活跃时段/配额/模式等）不影响已排定动作的具体目标 → 继续执行
    返回 (允许执行, 跳过原因)；允许执行时第二项为 None。
    """
    account_ids = campaign.account_ids or []
    if action.account_id not in account_ids:
        return False, "config_account_removed"
    if action.action_type == "post" and action.post_id:
        post = db.get(RedditPost, action.post_id)
        if post is not None:
            subreddits = campaign.subreddits or []
            if subreddits and post.subreddit not in subreddits:
                return False, "config_subreddit_removed"
    return True, None


def revalidate_campaign_content_config(db: Session, kind: str, item_id: int) -> bool:
    """发布扫描的 config_version 门控（阶段 3）：内容发放前重校验配置一致性。

    版本一致 → True；不一致但按影响分级仍允许 → True（继续发布）；
    已失效（账号/社区被移出）→ 内容与关联动作同步标作废/skipped，返回 False。
    """
    if kind == "post":
        content = db.get(RedditPost, item_id)
    else:
        content = db.get(RedditComment, item_id)
    if content is None or content.campaign_id is None:
        return True  # 非 campaign 内容不受配置版本约束
    campaign = db.get(RedditCampaign, content.campaign_id)
    if campaign is None:
        return True  # campaign 门控已拦
    action = (
        db.query(RedditCampaignAction)
        .filter(
            RedditCampaignAction.post_id == item_id
            if kind == "post"
            else RedditCampaignAction.comment_id == item_id
        )
        .first()
    )
    if action is None or action.config_version == campaign.config_version:
        return True
    allowed, reason = revalidate_action_config(db, action, campaign)
    if allowed:
        logger.info(
            "Campaign content #%s config mismatch but still allowed (%s)",
            item_id, campaign.config_version,
        )
        return True
    # 已失效 → 取消：内容标 rejected（发布链路不再领取），动作标 skipped 留审计
    now = now_bj()
    content.status = "rejected"
    content.error_message = f"配置变更后已失效（{reason}），内容作废"
    content.updated_at = now
    db.execute(
        text(
            """
            UPDATE reddit_campaign_actions
            SET status = 'skipped', skip_reason = :reason, result_status = 'config_version_mismatch',
                last_error_code = 'config_version_mismatch', updated_at = :now
            WHERE id = :id AND status IN ('pending', 'executing')
            """
        ),
        {"reason": reason, "now": now, "id": action.id},
    )
    db.commit()
    logger.warning(
        "Campaign content #%s invalidated by config change: %s", item_id, reason
    )
    return False


def execute_action(db: Session, action_id: int, attempt: int = 0, *, worker_id: str | None = None) -> dict:
    """执行单条点赞动作：领取（租约+代次）→ 前置校验 → 标记请求发出 → vote →
    投票日志 upsert + 终态同事务提交。

    结果分类（阶段 1 协议）：
    - 平台明确响应（429/503 限流）：confirmed_absent，预算内回 pending 延迟重试；
    - 平台明确拒绝（其他错误码）：confirmed_absent → failed；
    - 超时/网络错误/未知异常：请求可能已发出 → reconciling（对账分流，禁止自动重试）；
    - 成功：done + RedditVoteLog upsert + attempt responded 同事务。
    """
    generation = claim_action(db, action_id, worker_id=worker_id)
    if generation is None:
        return {"ok": False, "reason": "not_claimable"}
    action = db.get(RedditCampaignAction, action_id)
    if action is None:
        return {"ok": False, "reason": "missing"}

    def _finish(status: str, **kw) -> bool:
        return _finish_executing(db, action_id, generation, status, **kw)

    try:
        # ---- 发出外部请求前的最后校验（暂停协议停止边界）----
        campaign = (
            db.get(RedditCampaign, action.campaign_id) if action.campaign_id else None
        )
        if campaign is None or campaign.status != "running":
            # 暂停/结束/删除：已领取未发出 → 停止（回 pending），恢复后由扫描重取
            _release_unstarted(db, action_id, generation, "paused_before_request")
            return {"ok": False, "reason": "campaign_not_running"}

        profile = get_or_create_profile(db, action.site_id, action.account_id)
        if profile.risk_status != "normal":
            _finish("skipped", skip_reason="account_risk_warning", error_code="account_risk_warning")
            return {"ok": False, "reason": "account_risk_warning"}

        if not action.target_thing_id:
            _finish("failed", error_message="缺少目标 thing_id", error_code="missing_target")
            return {"ok": False, "reason": "missing_target"}

        account = db.query(SocialAccount).filter(SocialAccount.id == action.account_id).first()
        if account is None:
            _finish("failed", error_message="账号不存在", error_code="missing_account")
            return {"ok": False, "reason": "missing_account"}

        # config_version 执行前校验（阶段 3 影响分级）：点赞目标为具体 thing，
        # 社区/配额类变更不影响该目标 → 继续执行；账号被移出 → 不再允许执行。
        config_mismatch = campaign.config_version != action.config_version
        if config_mismatch:
            allowed, invalid_reason = revalidate_action_config(db, action, campaign)
            if not allowed:
                _finish(
                    "skipped",
                    skip_reason=invalid_reason,
                    result_status="config_version_mismatch",
                    error_code="config_version_mismatch",
                )
                return {"ok": False, "reason": invalid_reason}
            logger.warning(
                "Action config_version mismatch (allowed): action=%s action_v=%s campaign_v=%s",
                action_id,
                action.config_version,
                campaign.config_version,
            )

        client = get_reddit_client_for_account(account)

        # ---- 请求即将发出：持久化 request_started_at（先于外部调用提交）----
        if not _mark_request_started(db, action_id, generation):
            # 代次已被新领取覆盖：本次执行作废，外部请求不得发出
            return {"ok": False, "reason": "stale_generation"}

        try:
            client.vote(action.target_thing_id, 1)
        except RedditApiError as exc:
            if exc.status_code in _RETRYABLE_STATUS:
                # 平台明确限流响应 = 副作用未发生（confirmed_absent）→ 预算内回 pending
                wait = reddit_retry_delay(exc)
                if (
                    wait is not None
                    and action.execution_attempt_count < action.execution_max_attempts
                ):
                    now = now_bj()
                    db.execute(
                        text(
                            """
                            UPDATE reddit_campaign_actions
                            SET status = 'pending', planned_at = :next_run, available_at = :next_run,
                                lease_until = NULL, lease_owner = NULL,
                                result_status = 'confirmed_absent', last_error_code = 'retryable_rate_limit',
                                error_message = :msg, updated_at = :now
                            WHERE id = :id AND execution_lease_generation = :gen AND status = 'executing'
                            """
                        ),
                        {
                            "next_run": now + timedelta(seconds=wait),
                            "msg": (
                                f"限流({exc.status_code})，{wait}s 后自动重试"
                                f"（第 {action.execution_attempt_count}/{action.execution_max_attempts} 次）"
                                f"：{str(exc)[:200]}"
                            ),
                            "now": now,
                            "id": action_id,
                            "gen": generation,
                        },
                    )
                    _respond_attempt(
                        db, action_id, "execution", generation,
                        error_code="retryable_rate_limit", error_message=str(exc)[:300],
                    )
                    db.commit()
                    raise RedditActionRetryable(
                        str(exc)[:300],
                        status_code=exc.status_code or 429,
                        retry_after=wait,
                        attempt=attempt,
                    ) from exc
                _finish(
                    "failed",
                    error_message=f"限流且重试预算耗尽：{str(exc)[:300]}",
                    result_status="confirmed_absent",
                    error_code="retry_budget_exhausted",
                )
                return {"ok": False, "reason": "rate_limited_budget_exhausted"}
            # 其他平台明确拒绝 = 副作用未发生 → failed
            _finish(
                "failed",
                error_message=str(exc)[:500],
                result_status="confirmed_absent",
                error_code=f"reddit_rejected_{exc.status_code or 'unknown'}",
            )
            return {"ok": False, "reason": str(exc)[:200]}
        except Exception as exc:  # noqa: BLE001 — 超时/网络/未知：结果未知 → reconciling
            db.rollback()
            logger.warning(
                "Vote outcome unknown: action=%s gen=%s err=%s", action_id, generation, str(exc)[:200]
            )
            mark_action_reconciling(db, action_id, generation, f"请求结果未知：{str(exc)[:200]}")
            return {"ok": False, "reason": "outcome_unknown_reconciling"}

        # 投票效果验证：best-effort 快照投票后目标帖分数，失败不影响动作本身
        score_at_vote = None
        if action.target_thing_id.startswith("t3_"):
            try:
                item = client.get_post_by_id(action.target_thing_id, "")
                if item:
                    score_at_vote = int(item.get("score") or 0)
            except Exception:  # noqa: BLE001 — 快照属附属数据
                logger.debug("score_at_vote snapshot failed", exc_info=True)

        # ---- 终态事务：投票日志 upsert + done + attempt responded（同事务提交）----
        log = (
            db.query(RedditVoteLog)
            .filter(RedditVoteLog.account_id == action.account_id, RedditVoteLog.thing_id == action.target_thing_id)
            .first()
        )
        if log:
            log.direction = 1
            log.source = "campaign"
            log.campaign_action_id = action.id
            log.score_at_vote = score_at_vote
            log.error_message = None
        else:
            db.add(
                RedditVoteLog(
                    site_id=action.site_id,
                    account_id=action.account_id,
                    thing_id=action.target_thing_id,
                    direction=1,
                    score_at_vote=score_at_vote,
                    source="campaign",
                    campaign_action_id=action.id,
                )
            )
        db.flush()
        summary = {"score_at_vote": score_at_vote, "config_mismatch": config_mismatch}
        ok = _finish(
            "done",
            result_status="confirmed_success",
            external_object_id=action.target_thing_id,
            result_summary=summary,
        )
        if not ok:
            # fencing 拒绝（租约已被接管）：回滚投票日志写入，外部结果交由对账
            db.rollback()
            return {"ok": False, "reason": "fencing_rejected"}
        return {"ok": True, "action_id": action_id}
    except RedditActionRetryable:
        raise
    except Exception as exc:  # noqa: BLE001 — 前置校验外的未知异常：结果未知 → reconciling
        db.rollback()
        logger.exception("execute_action unexpected failure action=%s", action_id)
        try:
            mark_action_reconciling(db, action_id, generation, f"执行异常：{str(exc)[:200]}")
        except Exception:  # noqa: BLE001
            logger.exception("mark_action_reconciling failed action=%s", action_id)
        return {"ok": False, "reason": str(exc)[:200]}


# ============ 生成阶段（阶段 2：AI 生成出环到 generation 队列）============
def _try_enqueue_generation(action_id: int) -> bool:
    """generation 任务去重入队；broker/Redis 不可用静默降级（心跳重投兜底）。"""
    from app.tasks.reddit_campaign_tasks import generate_campaign_content
    from app.utils.rate_limiter import get_redis

    try:
        r = get_redis()
        key = f"enqueue:reddit:campaign_generation:{action_id}"
        if not r.set(key, "1", nx=True, ex=30 * 60):
            return False
        try:
            generate_campaign_content.apply_async(
                args=[action_id], task_id=f"reddit-campaign-gen-{action_id}"
            )
            return True
        except Exception:
            r.delete(key)
            raise
    except Exception:  # noqa: BLE001 — 入队失败不阻断规划，心跳重投拾取
        logger.debug("generation enqueue failed action=%s", action_id, exc_info=True)
        return False


def _claim_generate(
    db: Session, action_id: int, worker_id: str, now: datetime, lease_minutes: int
) -> int | None:
    """原子领取生成阶段：pending + (queued 或 生成租约过期) + 预算内 → generating。

    与执行阶段共享 lease_owner/lease_until 列，但两阶段绝不同时持有租约
    （生成阶段动作仍是 pending，执行阶段动作是 executing）——分时复用安全。
    返回新生成代次；不可领取返回 None。
    """
    row = db.execute(
        text(
            """
            UPDATE reddit_campaign_actions
            SET generation_status = 'generating',
                generation_lease_generation = generation_lease_generation + 1,
                lease_owner = :wid, lease_until = :lease_until, updated_at = :now
            WHERE id = :id AND status = 'pending'
              AND (generation_status = 'queued'
                   OR (generation_status = 'generating'
                       AND (lease_until IS NULL OR lease_until <= :now)))
              AND generation_attempt_count < generation_max_attempts
            RETURNING generation_lease_generation
            """
        ),
        {
            "wid": worker_id,
            "lease_until": now + timedelta(minutes=lease_minutes),
            "now": now,
            "id": action_id,
        },
    ).first()
    if row is None:
        return None
    generation = int(row[0])
    db.execute(
        text(
            """
            INSERT INTO reddit_campaign_action_attempts
                (action_id, phase, attempt_no, request_state, worker_id, created_at, updated_at)
            VALUES (:id, 'generation', :gen, 'request_pending', :wid, :now, :now)
            """
        ),
        {"id": action_id, "gen": generation, "wid": worker_id, "now": now},
    )
    db.execute(
        text(
            """
            UPDATE reddit_campaign_actions
            SET generation_attempt_count = :gen, updated_at = :now
            WHERE id = :id
            """
        ),
        {"gen": generation, "now": now, "id": action_id},
    )
    return generation


def _settle_generation_budget_exhausted(db: Session, action_id: int) -> bool:
    """领取失败后的预算判定：生成尝试已达上限 → 置 failed 终态，防心跳无限投递。"""
    now = now_bj()
    updated = db.execute(
        text(
            """
            UPDATE reddit_campaign_actions
            SET generation_status = 'failed', status = 'skipped', executed_at = :now,
                skip_reason = 'generation_retry_exhausted',
                last_error_code = 'generation_retry_exhausted',
                error_message = '生成重试预算耗尽，转人工处理',
                lease_until = NULL, lease_owner = NULL, updated_at = :now
            WHERE id = :id AND status = 'pending' AND generation_status != 'failed'
              AND generation_attempt_count >= generation_max_attempts
            """
        ),
        {"now": now, "id": action_id},
    ).rowcount
    db.commit()
    return updated == 1


def _release_generation(
    db: Session,
    action_id: int,
    generation: int,
    *,
    backoff_seconds: int = 300,
    error_code: str | None = None,
    message: str | None = None,
) -> None:
    """释放生成租约回 queued（AI 瞬时失败/预算未开/暂停等可恢复场景），
    available_at 退避，由心跳重投拾取。AI 生成是纯读外部 API（无外部副作用承诺），
    崩溃恢复直接重试安全——这是生成阶段无需 reconciling 的根本原因。
    """
    now = now_bj()
    db.execute(
        text(
            """
            UPDATE reddit_campaign_actions
            SET generation_status = 'queued', lease_until = NULL, lease_owner = NULL,
                available_at = :avail, last_error_code = :ec, error_message = :em,
                updated_at = :now
            WHERE id = :id AND generation_lease_generation = :gen
              AND generation_status = 'generating'
            """
        ),
        {
            "avail": now + timedelta(seconds=backoff_seconds),
            "ec": error_code,
            "em": (message[:500] if message else None),
            "now": now,
            "id": action_id,
            "gen": generation,
        },
    )
    _respond_attempt(db, action_id, "generation", generation, error_code=error_code, error_message=message)
    db.commit()


def release_generation_lease(
    db: Session,
    action_id: int,
    generation: int | None,
    *,
    backoff_seconds: int = 300,
    error_code: str | None = None,
    message: str | None = None,
) -> bool:
    """公有释放入口（任务层软超时处理器用）：代次为 None 时读取当前代次。"""
    if generation is None:
        generation = get_generation_lease_generation(db, action_id)
    if generation is None:
        db.rollback()
        return False
    try:
        _release_generation(
            db, action_id, generation,
            backoff_seconds=backoff_seconds, error_code=error_code, message=message,
        )
        return True
    except Exception:  # noqa: BLE001 — 释放失败交由心跳回收
        db.rollback()
        logger.debug("release generation lease failed action=%s", action_id, exc_info=True)
        return False


def _fail_generation(
    db: Session,
    action_id: int,
    generation: int,
    *,
    skip_reason: str,
    error_code: str,
    message: str | None = None,
) -> None:
    """生成终态失败（业务拒绝/上下文缺失）：generation_status=failed + status=skipped。"""
    now = now_bj()
    db.execute(
        text(
            """
            UPDATE reddit_campaign_actions
            SET generation_status = 'failed', status = 'skipped', executed_at = :now,
                skip_reason = :skip, last_error_code = :ec, error_message = :em,
                lease_until = NULL, lease_owner = NULL, updated_at = :now
            WHERE id = :id AND generation_lease_generation = :gen
              AND generation_status = 'generating'
            """
        ),
        {
            "skip": skip_reason[:200],
            "ec": error_code,
            "em": (message[:500] if message else None),
            "now": now,
            "id": action_id,
            "gen": generation,
        },
    )
    _respond_attempt(db, action_id, "generation", generation, error_code=error_code, error_message=message)
    db.commit()


def _mark_generation_request_started(db: Session, action_id: int, generation: int) -> bool:
    """DeepSeek 调用即将发出：持久化 request_started_at（独立短事务，先于外部调用提交）。"""
    updated = db.execute(
        text(
            """
            UPDATE reddit_campaign_action_attempts AS t
            SET request_started_at = :now, updated_at = :now
            FROM reddit_campaign_actions AS a
            WHERE t.action_id = a.id AND t.action_id = :id
              AND t.phase = 'generation' AND t.attempt_no = :gen
              AND a.generation_lease_generation = :gen
            """
        ),
        {"now": now_bj(), "id": action_id, "gen": generation},
    ).rowcount
    db.commit()
    return updated == 1


def get_generation_lease_generation(db: Session, action_id: int) -> int | None:
    """读取动作当前生成租约代次（软超时处理器定界用；动作不存在返回 None）。"""
    row = db.execute(
        text(
            "SELECT generation_lease_generation FROM reddit_campaign_actions WHERE id = :id"
        ),
        {"id": action_id},
    ).first()
    return int(row[0]) if row else None


def generate_action_content(db: Session, action_id: int, *, worker_id: str | None = None) -> dict:
    """generation 队列 worker 入口：原子领取 → 上下文校验 → AI 生成 → 单事务完成回写。

    事务边界：领取+attempts 预写在 AI 调用前提交（_mark_generation_request_started
    统一提交），外部调用不持有写事务；生成结果内存持有，内容行与动作回填在
    单个短事务内原子提交（阶段 1 P0-1 原则延续）。
    """
    wid = (worker_id or _worker_id())[:64]
    now = now_bj()
    generation = _claim_generate(db, action_id, wid, now, GENERATION_LEASE_MINUTES)
    if generation is None:
        db.rollback()
        if _settle_generation_budget_exhausted(db, action_id):
            logger.warning("Generation retry budget exhausted: action=%s", action_id)
            return {"ok": False, "reason": "generation_retry_exhausted"}
        return {"ok": False, "reason": "not_claimable"}

    action = db.get(RedditCampaignAction, action_id)
    if action is None:
        db.rollback()
        return {"ok": False, "reason": "missing"}
    campaign = db.get(RedditCampaign, action.campaign_id)
    if campaign is None or campaign.status != "running":
        # 暂停/结束停止边界：骨架尚未产生外部请求，释放回 queued 等恢复
        #（退避 30 分钟：暂停期间不反复投递，resume 后 available_at 已过自然重投）
        db.rollback()
        _release_generation(
            db, action_id, generation,
            backoff_seconds=1800, error_code="campaign_not_running",
            message="campaign 暂停/结束，生成挂起",
        )
        return {"ok": False, "reason": "campaign_not_running"}
    if action.cancel_requested_at is not None:
        db.rollback()
        _fail_generation(
            db, action_id, generation,
            skip_reason="cancel_requested", error_code="cancelled",
            message="收到取消请求，生成终止",
        )
        return {"ok": False, "reason": "cancelled"}

    site = db.get(ClientSite, campaign.site_id)
    account = db.get(SocialAccount, action.account_id)
    if account is None or site is None:
        db.rollback()
        _fail_generation(
            db, action_id, generation,
            skip_reason="missing_account", error_code="missing_account",
            message="账号或站点不存在，无法生成",
        )
        return {"ok": False, "reason": "missing_account"}

    # AI 预算（天级）在生成时点校验：骨架规划不再消耗预算
    if not _ai_budget_available(campaign.site_id)[0]:
        db.rollback()
        _release_generation(
            db, action_id, generation,
            backoff_seconds=1800, error_code="ai_budget_exhausted",
            message="站点 AI 日预算已用尽，退避后重试",
        )
        return {"ok": False, "reason": "ai_budget_exhausted"}

    # ---- DeepSeek 调用前最后一步：提交领取事务并持久化 request_started_at ----
    _mark_generation_request_started(db, action_id, generation)

    try:
        if action.action_type == "post":
            return _generate_post_for_action(db, action, campaign, site, account, generation)
        if action.action_type == "comment":
            return _generate_comment_for_action(db, action, campaign, site, account, generation)
        db.rollback()
        _fail_generation(
            db, action_id, generation,
            skip_reason="unsupported_action_type", error_code="unsupported_action_type",
            message=f"action_type={action.action_type} 不支持生成",
        )
        return {"ok": False, "reason": "unsupported_action_type"}
    except DeepSeekError as exc:
        db.rollback()
        logger.warning("Generation AI failed action=%s: %s", action_id, exc)
        _release_generation(
            db, action_id, generation,
            backoff_seconds=300, error_code="ai_transient_error", message=str(exc)[:500],
        )
        return {"ok": False, "reason": "ai_transient_error"}
    except HTTPException as exc:
        db.rollback()
        logger.warning("Generation rejected action=%s: %s", action_id, exc.detail)
        _fail_generation(
            db, action_id, generation,
            skip_reason=str(getattr(exc, "detail", exc))[:200],
            error_code="generation_rejected", message=str(getattr(exc, "detail", exc))[:500],
        )
        return {"ok": False, "reason": "generation_rejected"}
    except Exception as exc:  # noqa: BLE001 — 未知异常：AI 无副作用，退避重试（预算内）
        db.rollback()
        logger.exception("Generation unexpected error action=%s", action_id)
        _release_generation(
            db, action_id, generation,
            backoff_seconds=600, error_code="generation_error", message=str(exc)[:500],
        )
        return {"ok": False, "reason": "generation_error"}


def _generate_post_for_action(
    db: Session,
    action: RedditCampaignAction,
    campaign: RedditCampaign,
    site: ClientSite,
    account: SocialAccount,
    generation: int,
) -> dict:
    """帖子生成：社区轮转选择 → AI 流水线 → 风控闸门 → 内容行与动作回填单事务提交。"""
    from app.services.reddit_community_rotation import (
        per_sub_daily_cap,
        pick_angle_weighted,
        pick_post_type_weighted,
        pool_is_small,
        recent_angles,
        record_activity,
        select_subreddit,
    )
    from app.services.reddit_content import generate_post_content

    subs = _target_subreddits(db, campaign, account, include_promo=bool(campaign.product_id))
    if not subs:
        db.rollback()
        _fail_generation(
            db, action.id, generation,
            skip_reason="no_target_subreddit", error_code="no_target_subreddit",
            message="无目标社区，无法生成帖子",
        )
        return {"ok": False, "reason": "no_target_subreddit"}
    daily_total = int(campaign.daily_posts or 0) + int(campaign.daily_comments or 0)
    cap = per_sub_daily_cap(daily_total=daily_total, n_accounts=len(_pool_accounts(db, campaign)), n_subs=len(subs))
    apply_cd = not pool_is_small(n_accounts=len(_pool_accounts(db, campaign)), n_subs=len(subs))
    now = now_bj()
    subreddit = select_subreddit(
        db, account_id=account.id, candidates=subs, now=now,
        daily_cap=cap, apply_cooldown=apply_cd,
    )
    if not subreddit:
        db.rollback()
        _fail_generation(
            db, action.id, generation,
            skip_reason="no_subreddit_available", error_code="no_subreddit_available",
            message="社区轮转无可用社区（冷却/日帽），生成终止",
        )
        return {"ok": False, "reason": "no_subreddit_available"}

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
    recent_types = [
        r[0]
        for r in db.query(RedditPost.post_type)
        .filter(
            RedditPost.account_id == account.id,
            func.lower(RedditPost.subreddit) == subreddit.lower(),
        )
        .order_by(RedditPost.id.desc())
        .limit(6)
        .all()
        if r[0]
    ]
    post_type = pick_post_type_weighted(db, account.id, recent_types)
    angle_tag = pick_angle_weighted(db, account.id, recent_angles(db, account.id, subreddit))

    db.commit()  # 结束读取快照事务：AI 调用期间不持有写事务
    generated = generate_post_content(
        db,
        site=site,
        account=account,
        subreddit=subreddit,
        post_type=post_type,
        allow_product=allow_product,
        include_site_url=allow_product,
        product_id=campaign.product_id,
        angle_tag=angle_tag,
    )

    # 风控闸门（与原同步路径语义一致）
    ai_risk = generated.get("ai_risk")
    blocked_reason: str | None = None
    if campaign.content_mode == "auto":
        report = check_post_publish(
            db,
            site_id=campaign.site_id,
            account_id=account.id,
            subreddit=subreddit,
            body=generated["body"],
            site_url=generated["site_url"],
        )
        if ai_risk == "likely_ai":
            blocked_reason = "ai_detected_rewrite_failed"
        elif not report.ok:
            blocked_reason = report.errors[0] if report.errors else "risk_blocked"

    # ---- 单个短事务同提交：内容行 + 动作回填（fencing 守卫校验生成代次）----
    post = RedditPost(
        site_id=campaign.site_id,
        account_id=account.id,
        post_type=post_type,
        subreddit=subreddit,
        keyword=generated["keyword"],
        title=generated["title"],
        body=generated["body"],
        site_url=generated["site_url"],
        content_intent=generated["intent"],
        campaign_id=campaign.id,
        narrative_angle=generated.get("angle"),
        ai_risk=generated.get("ai_risk"),
        ai_score=generated.get("ai_score"),
    )
    if blocked_reason:
        post.status = "pending_review"
    elif campaign.content_mode == "auto":
        post.status = "approved"
        post.scheduled_at = action.planned_at or now_bj()
    else:
        post.status = "pending_review"
    db.add(post)
    db.flush()  # 取 post.id，不提交
    action_status = "skipped" if blocked_reason else "pending"
    updated = db.execute(
        text(
            """
            UPDATE reddit_campaign_actions
            SET post_id = :pid, generation_status = 'ready', status = :st,
                skip_reason = :skip, lease_until = NULL, lease_owner = NULL,
                available_at = NULL, updated_at = :now
            WHERE id = :id AND generation_lease_generation = :gen
              AND generation_status = 'generating'
            """
        ),
        {
            "pid": post.id,
            "st": action_status,
            "skip": blocked_reason,
            "now": now_bj(),
            "id": action.id,
            "gen": generation,
        },
    ).rowcount
    if updated != 1:
        # fencing 拒绝：生成租约已被接管（领取互斥下不应发生），内容行随之丢弃
        db.rollback()
        logger.warning(
            "Generation fencing rejected terminal write: action=%s gen=%s", action.id, generation
        )
        return {"ok": False, "reason": "fencing_rejected"}
    _respond_attempt(
        db, action.id, "generation", generation,
        external_object_id=f"reddit_post:{post.id}",
        result_summary={"ai_risk": ai_risk or "", "blocked": blocked_reason or ""},
    )
    db.commit()
    record_activity(
        db,
        site_id=campaign.site_id,
        account_id=account.id,
        subreddit=subreddit,
        angle=generated.get("angle"),
        at=now,
    )
    _ai_budget_consume(campaign.site_id)
    if post.status == "approved" and post.scheduled_at is not None:
        # ETA 直投：到点即发，发布扫描降级为兜底
        _try_direct_dispatch(db, "post", post.id, post.scheduled_at)
    logger.info(
        "Campaign post generated: action=%s post=%s blocked=%s",
        action.id, post.id, blocked_reason or "-",
    )
    return {"ok": True, "action_id": action.id, "post_id": post.id}


def _generate_comment_for_action(
    db: Session,
    action: RedditCampaignAction,
    campaign: RedditCampaign,
    site: ClientSite,
    account: SocialAccount,
    generation: int,
) -> dict:
    """评论生成：候选快照复用（免重复抓取）→ API 层生成链路 → 风控闸门 →
    campaign 归属与动作回填单事务提交。"""
    from app.api.v1.reddit import _generate_comment_for_url

    context: dict = {}
    if action.generation_context:
        try:
            context = json.loads(action.generation_context)
        except Exception:  # noqa: BLE001 — 坏快照退化为空上下文（生成链路自行抓取）
            context = {}

    db.commit()  # 结束读取快照事务：AI/抓取调用期间不持有写事务
    out = _generate_comment_for_url(
        db,
        SimpleNamespace(site=site),
        account.id,
        action.target_post_url or context.get("url") or "",
        include_site_url=False,  # campaign 评论为纯互动，不带链接（链接策略由发帖承担）
        discover_source="campaign",
        keyword=context.get("keyword") or None,
        known_title=context.get("title") or None,
        known_body=context.get("body") or None,
    )

    comment = db.query(RedditComment).filter(RedditComment.id == out.id).first()
    if comment is None:
        db.rollback()
        _release_generation(
            db, action.id, generation,
            backoff_seconds=600, error_code="comment_missing",
            message="生成链路返回的内容行缺失，退避重试",
        )
        return {"ok": False, "reason": "comment_missing"}

    # 风控闸门 + 发布状态（与原同步路径语义一致）：
    # auto 风控拦截 → 内容待审 + 动作 skipped；auto 通过 → approved + scheduled_at；
    # review 模式 → 内容待审 + 动作 pending。
    skip_reason: str | None = None
    if campaign.content_mode == "auto":
        report = check_comment_publish(
            db, site_id=campaign.site_id, account_id=account.id, body=comment.body
        )
        if comment.ai_risk == "likely_ai":
            # AI 复检多轮重写仍未过：降级人工审核，不自动发布
            comment.status = "pending_review"
            action_status = "skipped"
            skip_reason = "ai_detected_rewrite_failed"
        elif report.ok:
            comment.status = "approved"
            comment.scheduled_at = action.planned_at or now_bj()
            action_status = "pending"
        else:
            comment.status = "pending_review"  # 风控不过：留待审人工兜底
            action_status = "skipped"
            skip_reason = (report.errors[0] if report.errors else "risk_blocked")[:200]
    else:
        comment.status = "pending_review"
        action_status = "pending"
    comment.campaign_id = campaign.id
    db.flush()

    updated = db.execute(
        text(
            """
            UPDATE reddit_campaign_actions
            SET comment_id = :cid, generation_status = 'ready', status = :st,
                skip_reason = :skip, lease_until = NULL, lease_owner = NULL,
                available_at = NULL, updated_at = :now
            WHERE id = :id AND generation_lease_generation = :gen
              AND generation_status = 'generating'
            """
        ),
        {
            "cid": comment.id,
            "st": action_status,
            "skip": skip_reason,
            "now": now_bj(),
            "id": action.id,
            "gen": generation,
        },
    ).rowcount
    if updated != 1:
        db.rollback()
        logger.warning(
            "Generation fencing rejected terminal write: action=%s gen=%s", action.id, generation
        )
        return {"ok": False, "reason": "fencing_rejected"}
    _respond_attempt(
        db, action.id, "generation", generation,
        external_object_id=f"reddit_comment:{comment.id}",
        result_summary={"ai_risk": comment.ai_risk or "", "blocked": skip_reason or ""},
    )
    db.commit()
    _ai_budget_consume(campaign.site_id)
    if comment.status == "approved" and comment.scheduled_at is not None:
        _try_direct_dispatch(db, "comment", comment.id, comment.scheduled_at)
    logger.info(
        "Campaign comment generated: action=%s comment=%s blocked=%s",
        action.id, comment.id, skip_reason or "-",
    )
    return {"ok": True, "action_id": action.id, "comment_id": comment.id}


def requeue_stale_generation_actions(db: Session, limit: int = 20) -> int:
    """心跳重投：queued（可用）或 generating（租约过期）的骨架动作重新投递生成。

    AI 生成无外部副作用承诺：generating 崩溃直接回 queued 重试（无需对账），
    旧 attempt 回写 responded 标记回收；campaign 非 running 的动作跳过
    （暂停即停，resume 后 available_at 已过自然重投）。消息丢失（骨架已落库
    但投递失败/worker 丢弃）由此兜底——这正是心跳存在的意义。
    """
    now = now_bj()
    # 短窗口滚动规划：窗口外的骨架暂不生成（心跳到点自然拾取），减少作废浪费
    lead = settings.CAMPAIGN_GENERATION_LEAD_MINUTES
    gen_cutoff = now + timedelta(minutes=lead) if lead > 0 else None
    rows = db.execute(
        text(
            """
            SELECT a.id, a.generation_status, a.generation_lease_generation
            FROM reddit_campaign_actions a
            JOIN reddit_campaigns c ON c.id = a.campaign_id
            WHERE a.status = 'pending'
              AND a.action_type IN ('post', 'comment')
              AND c.status = 'running'
              AND (:gen_cutoff IS NULL OR a.planned_at IS NULL OR a.planned_at <= :gen_cutoff)
              AND (
                    (a.generation_status = 'queued'
                     AND (a.available_at IS NULL OR a.available_at <= :now))
                 OR (a.generation_status = 'generating'
                     AND (a.lease_until IS NULL OR a.lease_until <= :now))
                  )
            ORDER BY a.planned_at ASC
            LIMIT :limit
            """
        ),
        {"now": now, "limit": limit, "gen_cutoff": gen_cutoff},
    ).all()
    requeued = 0
    for action_id, gen_status, gen in rows:
        if gen_status == "generating":
            # 原子失效旧租约 → queued（同事务回写旧 attempt）
            updated = db.execute(
                text(
                    """
                    UPDATE reddit_campaign_actions
                    SET generation_status = 'queued', lease_until = NULL, lease_owner = NULL,
                        last_error_code = 'generation_lease_expired', updated_at = :now
                    WHERE id = :id AND status = 'pending' AND generation_status = 'generating'
                      AND (lease_until IS NULL OR lease_until <= :now)
                    """
                ),
                {"now": now, "id": action_id},
            ).rowcount
            if updated != 1:
                db.rollback()
                continue
            _respond_attempt(
                db, action_id, "generation", gen, error_code="generation_lease_expired"
            )
            db.commit()
        if _try_enqueue_generation(action_id):
            requeued += 1
    return requeued


# ============ tick 主流程 ============
def _generation_due(planned_at: datetime | None) -> bool:
    """短窗口滚动规划（阶段 3）：仅当计划执行时刻落入生成提前窗口内才触发生成。

    骨架仍整日规划（纯 DB、保留配额语义），但 AI 生成推迟到执行前 N 分钟——
    暂停/改配置时最多作废窗口内的已生成内容，而非整日。0 = 关闭（立即生成）。
    """
    if planned_at is None:
        return True
    lead = settings.CAMPAIGN_GENERATION_LEAD_MINUTES
    if lead <= 0:
        return True
    # planned_at 可能来自 DB 重读（timestamptz → aware）：统一转北京墙钟 naive 再比较，
    # 否则 expire_on_commit 下 commit 后访问属性触发重载 → TypeError（负载测试实测抓出）
    return as_bj_naive(planned_at) <= now_bj() + timedelta(minutes=lead)


def _plan_posts(db: Session, campaign: RedditCampaign, pool, now, remaining, hour_start, hour_end, stats) -> int:
    """帖子骨架规划（纯 DB）：AI 生成出环到 generation 队列，AI 预算在生成时点校验。"""
    created = 0
    for account in pool:
        if created >= remaining or created >= MAX_CONTENT_PER_TICK:
            break
        profile = get_or_create_profile(db, campaign.site_id, account.id)
        usage = get_account_daily_usage(db, account.id)
        if profile.daily_post_limit and usage["posts"] >= profile.daily_post_limit:
            stats["skipped"] += 1
            continue
        planned_at = _plan_time(db, account.id, now, hour_start, hour_end)
        if planned_at.date() != now.date():
            continue  # 同账号间隔顺延越出今日窗口：留给明天自己的配额
        action = _create_campaign_post(db, campaign, account, planned_at)
        if action is None:
            continue  # dedup 冲突（当日已规划），下个 tick 重试
        created += 1
        if _generation_due(action.planned_at):
            _try_enqueue_generation(action.id)
    return created


def _plan_comments(db: Session, campaign: RedditCampaign, pool, now, remaining, hour_start, hour_end, stats) -> int:
    """评论骨架规划（纯 DB）：候选来自 Redis 发现缓存，发现调用出环到 serp 队列。"""
    created = 0
    for account in pool:
        if created >= remaining or created >= MAX_CONTENT_PER_TICK:
            break
        profile = get_or_create_profile(db, campaign.site_id, account.id)
        usage = get_account_daily_usage(db, account.id)
        if profile.daily_comment_limit and usage["comments"] >= profile.daily_comment_limit:
            stats["skipped"] += 1
            continue
        planned_at = _plan_time(db, account.id, now, hour_start, hour_end)
        if planned_at.date() != now.date():
            continue  # 同账号间隔顺延越出今日窗口：留给明天自己的配额
        candidate = _pop_comment_candidate(db, campaign, account)
        if candidate is None:
            continue  # 缓存空：已异步触发发现补池，下个 tick 消费
        action = _create_campaign_comment(db, campaign, account, planned_at, candidate)
        if action is None:
            continue  # dedup 冲突（该账号已评论/已计划同一目标）
        created += 1
        if _generation_due(action.planned_at):
            _try_enqueue_generation(action.id)
    return created


def _tick_campaign(db: Session, campaign: RedditCampaign, now: datetime) -> dict:
    stats = {"posts": 0, "comments": 0, "upvotes": 0, "skipped": 0}
    pool = _pool_accounts(db, campaign)
    if not pool:
        return stats
    hour_start = int(campaign.active_hour_start or 0)
    hour_end = int(23 if campaign.active_hour_end is None else campaign.active_hour_end)
    # 今日活跃窗口已过：今天不再排新动作（把今天配额顺延进明天窗口会挤占
    # 明天的一轮；且 end_date=今天的动作排到明天将永远没有机会执行）
    if not _can_plan_today(now, hour_start, hour_end):
        return stats
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


TICK_HEARTBEAT_KEY = "heartbeat:reddit:campaign_tick"
TICK_STALE_MINUTES = 20  # tick 计划 15 分钟一轮，超过 20 分钟无心跳视为停摆
TICK_HEARTBEAT_TTL = 2 * 60 * 60  # beat 宕机后键自动过期，ops/告警显示 missing


def write_campaign_heartbeat() -> None:
    """写入全局 tick 心跳（Redis）。Redis 不可用时静默跳过，不影响调度主流程。"""
    from app.utils.rate_limiter import get_redis

    try:
        get_redis().set(TICK_HEARTBEAT_KEY, now_bj().isoformat(), ex=TICK_HEARTBEAT_TTL)
    except Exception:  # noqa: BLE001 — 心跳属可观测附属，失败只记 debug
        logger.debug("campaign tick heartbeat write failed", exc_info=True)


def read_campaign_tick_heartbeat() -> dict:
    """读取 tick 心跳状态，供心跳告警任务与 ops 接口共用。

    返回 {last_run_at, age_minutes, stale, reason}；stale=True 时 reason 给出原因。
    """
    from app.utils.rate_limiter import get_redis

    try:
        raw = get_redis().get(TICK_HEARTBEAT_KEY)
    except Exception:  # noqa: BLE001
        return {"last_run_at": None, "age_minutes": None, "stale": True, "reason": "redis_unavailable"}
    if not raw:
        return {"last_run_at": None, "age_minutes": None, "stale": True, "reason": "missing"}
    try:
        last = datetime.fromisoformat(raw.decode() if isinstance(raw, bytes) else str(raw))
    except ValueError:
        return {"last_run_at": None, "age_minutes": None, "stale": True, "reason": "corrupted"}
    age_minutes = (now_bj() - last).total_seconds() / 60
    stale = age_minutes > TICK_STALE_MINUTES
    return {
        "last_run_at": last.isoformat(),
        "age_minutes": round(age_minutes, 1),
        "stale": stale,
        "reason": "stale" if stale else None,
    }


def run_campaign_tick(db: Session) -> dict:
    """tick 主入口：扫描活跃 campaign → 计划动作 → 对账 → 回收僵尸。"""
    import time as _time

    tick_started = _time.perf_counter()
    now = now_bj()
    today = now.date()
    campaigns = db.query(RedditCampaign).filter(RedditCampaign.status == "running").all()

    # 到期自动结束：end_date 已过的 running campaign 置 ended，避免永远悬挂在运行中
    expired = [c for c in campaigns if c.end_date and c.end_date < today]
    if expired:
        for c in expired:
            c.status = "ended"
        try:
            db.commit()
            logger.info(
                "Campaign auto-ended (end_date passed): ids=%s", [c.id for c in expired]
            )
        except Exception:  # noqa: BLE001 — 并发删除等场景不阻断本轮 tick
            db.rollback()
            expired = []
        campaigns = [c for c in campaigns if c not in expired]

    active = [c for c in campaigns if c.start_date <= today <= c.end_date]

    reconcile_campaign_actions(db)
    recovered = recover_stale_actions(db)
    if recovered.get("failed") or recovered.get("reconciling"):
        logger.info("Campaign tick recovered stale actions: %s", recovered)
    reconcile_uncertain_actions(db)

    # 表现反馈校准已移至每日 beat 任务 reddit_community_feedback_calibration
    # （社区表现变化慢，无需每 15 分钟全量扫描；且扫描查询已加 30 天回看窗口）

    summary = {"campaigns": len(active), "posts": 0, "comments": 0, "upvotes": 0, "skipped": 0}
    for campaign in active:
        campaign_id = campaign.id
        campaign_started = _time.perf_counter()
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
        # 阶段 0 埋点：每 campaign 规划耗时（与 AI/发现调用延迟绑定的结构性问题的量化依据）
        campaign_elapsed_ms = round((_time.perf_counter() - campaign_started) * 1000, 1)
        logger.info(
            "Campaign #%s tick: posts=%s comments=%s upvotes=%s skipped=%s (%.1fms)",
            campaign.id,
            stats["posts"],
            stats["comments"],
            stats["upvotes"],
            stats["skipped"],
            campaign_elapsed_ms,
            extra={
                "event": "campaign_planned",
                "campaign_id": campaign.id,
                "site_id": campaign.site_id,
                "elapsed_ms": campaign_elapsed_ms,
                "detail": stats,
            },
        )
    # 阶段 0 埋点：tick 总耗时（验收标准：阶段 2 后 tick 耗时与 AI 调用数解耦、千级 campaign P95 < 60s）
    tick_elapsed_ms = round((_time.perf_counter() - tick_started) * 1000, 1)
    logger.info(
        "Campaign tick complete: %s (%.1fms)",
        summary,
        tick_elapsed_ms,
        extra={
            "event": "tick_complete",
            "elapsed_ms": tick_elapsed_ms,
            "detail": summary,
        },
    )
    return summary


# ============ 数据保留（阶段 3）：actions / attempts 定期归档清理 ============
def cleanup_expired_campaign_data(
    db: Session,
    *,
    action_retention_days: int = 365,
    attempt_retention_days: int = 180,
    batch_size: int = 5000,
) -> dict:
    """清理过期的动作台账与尝试审计（分批删除，避免长事务锁表）。

    - attempts：保留 N 天（默认 180），无论动作是否仍存活；
    - actions：终态（done/failed/skipped）保留 N 天（默认 365）后删除，
      FK CASCADE 级联清理其剩余 attempts；needs_review 留待人工，不自动删；
      executing/pending/reconciling 属活跃状态，永不清理。
    保留天数 ≤0 表示永久保留。先删 attempts（短保留期），再删 actions。
    """
    now = now_bj()
    result = {"attempts_deleted": 0, "actions_deleted": 0}
    if attempt_retention_days > 0:
        cutoff = now - timedelta(days=attempt_retention_days)
        while True:
            deleted = db.execute(
                text(
                    """
                    DELETE FROM reddit_campaign_action_attempts
                    WHERE id IN (
                        SELECT id FROM reddit_campaign_action_attempts
                        WHERE created_at < :cutoff LIMIT :batch
                    )
                    """
                ),
                {"cutoff": cutoff, "batch": batch_size},
            ).rowcount
            db.commit()
            result["attempts_deleted"] += deleted
            if deleted < batch_size:
                break
    if action_retention_days > 0:
        cutoff = now - timedelta(days=action_retention_days)
        while True:
            deleted = db.execute(
                text(
                    """
                    DELETE FROM reddit_campaign_actions
                    WHERE id IN (
                        SELECT id FROM reddit_campaign_actions
                        WHERE status IN ('done', 'failed', 'skipped')
                          AND created_at < :cutoff LIMIT :batch
                    )
                    """
                ),
                {"cutoff": cutoff, "batch": batch_size},
            ).rowcount
            db.commit()
            result["actions_deleted"] += deleted
            if deleted < batch_size:
                break
    return result


# ============ 扩池：相似社区扩散（beat 每日执行 / 手动触发）============
def run_community_expansion(db: Session, *, per_site_accounts: int = 2, max_new: int = 3) -> dict:
    """对每个站点的 Reddit 账号，从优质人设社区扩散相似社区并校验入库。

    仅处理已有人设社区、且能取到 Zernio 客户端的账号；单账号失败不影响其他。
    """
    from app.services.reddit_community_rotation import expand_similar_communities
    from app.services.ai_writer import get_ai_client

    summary = {"accounts": 0, "added": 0, "errors": 0}
    site_ids = [r[0] for r in db.query(ClientSite.id).all()]
    for site_id in site_ids:
        accounts = (
            db.query(SocialAccount)
            .filter(
                SocialAccount.site_id == site_id,
                SocialAccount.platform == "reddit",
                SocialAccount.is_active.is_(True),
            )
            .limit(per_site_accounts)
            .all()
        )
        for account in accounts:
            summary["accounts"] += 1
            try:
                profile = get_or_create_profile(db, site_id, account.id)
                from app.services.reddit_persona import parse_persona

                interests = parse_persona(profile.persona, config=profile.persona_config).interests
                client = get_reddit_client_for_account(account)
                try:
                    ai = get_ai_client()
                except Exception:  # noqa: BLE001 — 无 AI 时跳过扩散
                    ai = None
                added = expand_similar_communities(
                    db,
                    site_id=site_id,
                    account_id=account.id,
                    client=client,
                    ai=ai,
                    interests=interests,
                    max_new=max_new,
                )
                summary["added"] += len(added)
            except Exception:  # noqa: BLE001 — 单账号失败不阻断
                db.rollback()
                summary["errors"] += 1
                logger.exception(
                    "Community expansion failed site=%s account=%s", site_id, account.id
                )
    return summary
