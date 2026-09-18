"""Reddit 运营风控服务。

对应《Reddit赋能Google SEO+Google AI搜索曝光》方案：
- 账号矩阵分层（养号号/种草号/答疑号）与频次管控（第九章-4 智能频次管控）
- 外链植入规范（第四章-4：单帖最多 1 条外链、同链接不重复多发、新号仅评论植入）
- 内容去重（第九章-4：AI 生成内容差异化，规避重复度检测）
- 社区规则匹配（第二章-4：分社区定制策略，禁外链/固定日自推广）
- 风控预警（第九章-4：低互动自动预警，切换养号模式）
"""
from __future__ import annotations

import difflib
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.models.reddit import (
    RedditAccountProfile,
    RedditComment,
    RedditCommunity,
    RedditKeyword,
    RedditPost,
)

# 各角色默认限额（对齐方案：日均 3-5 篇优质帖、15-20 条精准评论）
DEFAULT_ROLE_LIMITS: dict[str, dict[str, int]] = {
    "warmup": {"daily_post_limit": 0, "daily_comment_limit": 10},
    "seeding": {"daily_post_limit": 3, "daily_comment_limit": 15},
    "expert": {"daily_post_limit": 2, "daily_comment_limit": 20},
}

# 方案：Karma 达标 300+ 轻度植入，500+ 稳定推广
KARMA_SOFT_THRESHOLD = 300
KARMA_FULL_THRESHOLD = 500

# 内容查重阈值：与同账号近 7 天内容相似度超过该值视为模板化重复
SIMILARITY_THRESHOLD = 0.85
DEDUP_WINDOW_DAYS = 7


class RiskViolation(Exception):
    """发布被风控拦截。errors 为阻断原因，warnings 为提示信息。"""

    def __init__(self, errors: list[str], warnings: list[str] | None = None) -> None:
        self.errors = errors
        self.warnings = warnings or []
        super().__init__("；".join(errors))


@dataclass
class RiskReport:
    ok: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


# ============ 账号矩阵档案 ============
def get_or_create_profile(db: Session, site_id: int, account_id: int) -> RedditAccountProfile:
    """获取账号矩阵档案；不存在则按养号号默认档创建。"""
    profile = (
        db.query(RedditAccountProfile)
        .filter(RedditAccountProfile.site_id == site_id, RedditAccountProfile.account_id == account_id)
        .first()
    )
    if profile:
        return profile
    limits = DEFAULT_ROLE_LIMITS["warmup"]
    profile = RedditAccountProfile(
        site_id=site_id,
        account_id=account_id,
        role="warmup",
        stage="warmup_week1_2",
        karma=0,
        daily_post_limit=limits["daily_post_limit"],
        daily_comment_limit=limits["daily_comment_limit"],
        risk_status="normal",
    )
    db.add(profile)
    db.commit()
    db.refresh(profile)
    return profile


def get_profiles_for_site(db: Session, site_id: int) -> list[RedditAccountProfile]:
    return (
        db.query(RedditAccountProfile)
        .filter(RedditAccountProfile.site_id == site_id)
        .order_by(RedditAccountProfile.id.asc())
        .all()
    )


def apply_role_defaults(profile: RedditAccountProfile) -> None:
    """按角色写入默认限额（仅当用户未显式自定义时使用）。"""
    limits = DEFAULT_ROLE_LIMITS.get(profile.role, DEFAULT_ROLE_LIMITS["warmup"])
    profile.daily_post_limit = limits["daily_post_limit"]
    profile.daily_comment_limit = limits["daily_comment_limit"]


def mark_warning(db: Session, profile: RedditAccountProfile, reason: str) -> None:
    profile.risk_status = "warning"
    profile.risk_reason = reason
    profile.last_warning_at = datetime.utcnow()
    db.commit()


def clear_warning(db: Session, profile: RedditAccountProfile) -> None:
    profile.risk_status = "normal"
    profile.risk_reason = None
    db.commit()


# ============ 频次统计 ============
def get_account_daily_usage(db: Session, account_id: int) -> dict[str, int]:
    """统计账号当日（UTC）已发布的帖子 / 评论数量。"""
    day_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    posts = (
        db.query(RedditPost)
        .filter(
            RedditPost.account_id == account_id,
            RedditPost.status.in_(["posted", "posting"]),
            RedditPost.published_at >= day_start,
        )
        .count()
    )
    comments = (
        db.query(RedditComment)
        .filter(
            RedditComment.account_id == account_id,
            RedditComment.status.in_(["posted", "posting"]),
            RedditComment.published_at >= day_start,
        )
        .count()
    )
    return {"posts": posts, "comments": comments}


def get_subreddit_daily_usage(db: Session, site_id: int, subreddit: str) -> int:
    """统计当日（UTC）该站点在同一子版块已发布的帖子数。"""
    day_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    return (
        db.query(RedditPost)
        .filter(
            RedditPost.site_id == site_id,
            RedditPost.subreddit == subreddit,
            RedditPost.status.in_(["posted", "posting"]),
            RedditPost.published_at >= day_start,
        )
        .count()
    )


# ============ 风控校验 ============
def check_post_publish(
    db: Session,
    *,
    site_id: int,
    account_id: int,
    subreddit: str,
    body: str,
    site_url: str | None,
    exclude_post_id: int | None = None,
) -> RiskReport:
    """帖子发布前综合风控校验。返回 RiskReport；阻断项在 errors 非空时通过 RiskViolation 抛出由调用方决定。"""
    errors: list[str] = []
    warnings: list[str] = []

    profile = get_or_create_profile(db, site_id, account_id)

    # 1) 风控预警状态：暂停营销动作，切换养号模式
    if profile.risk_status == "warning":
        errors.append(f"账号处于风控预警状态（{profile.risk_reason or '低互动/疑似限流'}），已暂停营销动作，请先养号并解除预警")

    # 2) 养号阶段：周 1-2 禁发帖；周 3-4 仅纯干货无链接帖
    if profile.stage == "warmup_week1_2":
        errors.append("账号处于养号第 1-2 周阶段，禁止发帖（仅允许点赞/评论互动）")
    elif profile.stage == "warmup_week3_4" and site_url:
        errors.append("账号处于养号第 3-4 周阶段，仅可发布无链接纯干货帖")

    # 3) Karma 门槛：300+ 才允许轻度植入链接
    if site_url and profile.karma < KARMA_SOFT_THRESHOLD:
        errors.append(f"账号 Karma（{profile.karma}）未达 {KARMA_SOFT_THRESHOLD}，不允许植入站点链接")

    # 4) 频次管控：账号日限额
    usage = get_account_daily_usage(db, account_id)
    if usage["posts"] >= profile.daily_post_limit:
        errors.append(
            f"已达账号每日发帖上限（{usage['posts']}/{profile.daily_post_limit}），请切换账号或明日再发"
        )

    # 5) 社区规则匹配
    community = (
        db.query(RedditCommunity)
        .filter(RedditCommunity.site_id == site_id, RedditCommunity.name == subreddit)
        .first()
    )
    if community:
        if not community.is_active:
            warnings.append(f"r/{subreddit} 在社区库中已标记停用，建议换社区")
        if site_url and not community.allows_links:
            errors.append(f"r/{subreddit} 版规禁止外链，请去掉站点链接")
        if site_url and community.promo_weekday is not None and community.promo_weekday != datetime.utcnow().weekday():
            weekdays = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
            errors.append(
                f"r/{subreddit} 仅{weekdays[community.promo_weekday]}允许自推广，今日请发布无链接内容"
            )
        if get_subreddit_daily_usage(db, site_id, subreddit) >= community.daily_post_limit:
            errors.append(f"r/{subreddit} 已达每日发帖上限（{community.daily_post_limit}）")

    # 6) 外链规范：同一条链接不重复多发不同子版块（7 天窗口）
    if site_url:
        week_ago = datetime.utcnow() - timedelta(days=DEDUP_WINDOW_DAYS)
        dup = (
            db.query(RedditPost)
            .filter(
                RedditPost.site_id == site_id,
                RedditPost.site_url == site_url,
                RedditPost.status.in_(["posted", "posting", "approved"]),
                RedditPost.created_at >= week_ago,
                RedditPost.id != (exclude_post_id or 0),
            )
            .first()
        )
        if dup:
            errors.append(f"同一站点链接近 {DEDUP_WINDOW_DAYS} 天已在 r/{dup.subreddit} 使用过，请勿重复多发")

    # 7) 内容去重：与同账号近 7 天帖子正文相似度检测
    week_ago = datetime.utcnow() - timedelta(days=DEDUP_WINDOW_DAYS)
    recent = (
        db.query(RedditPost)
        .filter(
            RedditPost.account_id == account_id,
            RedditPost.created_at >= week_ago,
            RedditPost.id != (exclude_post_id or 0),
        )
        .all()
    )
    norm = " ".join((body or "").lower().split())
    for other in recent:
        ratio = difflib.SequenceMatcher(None, norm, " ".join((other.body or "").lower().split())).ratio()
        if ratio >= SIMILARITY_THRESHOLD:
            errors.append(f"内容与近期帖子 #{other.id} 相似度过高（{ratio:.0%}），疑似模板化重复，请改写后发布")
            break

    return RiskReport(ok=not errors, errors=errors, warnings=warnings)


def check_comment_publish(
    db: Session,
    *,
    site_id: int,
    account_id: int,
    body: str,
) -> RiskReport:
    """评论发布前风控校验。"""
    errors: list[str] = []
    warnings: list[str] = []

    profile = get_or_create_profile(db, site_id, account_id)

    if profile.risk_status == "warning":
        errors.append(f"账号处于风控预警状态（{profile.risk_reason or '低互动/疑似限流'}），已暂停营销动作")

    usage = get_account_daily_usage(db, account_id)
    if usage["comments"] >= profile.daily_comment_limit:
        errors.append(
            f"已达账号每日评论上限（{usage['comments']}/{profile.daily_comment_limit}），杜绝集中刷屏"
        )

    week_ago = datetime.utcnow() - timedelta(days=DEDUP_WINDOW_DAYS)
    recent = (
        db.query(RedditComment)
        .filter(
            RedditComment.account_id == account_id,
            RedditComment.created_at >= week_ago,
        )
        .all()
    )
    norm = " ".join((body or "").lower().split())
    for other in recent:
        ratio = difflib.SequenceMatcher(None, norm, " ".join((other.body or "").lower().split())).ratio()
        if ratio >= SIMILARITY_THRESHOLD:
            errors.append(f"评论与近期评论 #{other.id} 相似度过高（{ratio:.0%}），请差异化改写")
            break

    return RiskReport(ok=not errors, errors=errors, warnings=warnings)


def ensure_karma_stage_consistency(db: Session, profile: RedditAccountProfile) -> None:
    """按 Karma 阈值自动推进养号阶段（warmup 阶段专用）。"""
    if profile.role != "warmup":
        return
    if profile.stage == "warmup_week1_2" and profile.karma >= 50:
        profile.stage = "warmup_week3_4"
    elif profile.stage == "warmup_week3_4" and profile.karma >= KARMA_SOFT_THRESHOLD:
        profile.stage = "ready"
    elif profile.stage == "ready" and profile.karma >= KARMA_FULL_THRESHOLD:
        profile.stage = "active"


# ============ 预置数据 ============
DEFAULT_COMMUNITIES: list[dict] = [
    # 核心垂直社区（方案 第三章-2）
    {"name": "BabyMonitoring", "category": "core", "priority": 1, "best_hour_utc": 14,
     "rules_note": "低辐射监护器核心社区，允许讨论产品"},
    {"name": "Parenting", "category": "core", "priority": 1, "best_hour_utc": 14,
     "rules_note": "大社区，禁止硬广，外链需高度场景化", "allows_links": True},
    {"name": "NewParents", "category": "core", "priority": 1, "best_hour_utc": 15,
     "rules_note": "新手父母答疑为主，禁止纯商家内容"},
    {"name": "BabyGear", "category": "core", "priority": 2, "best_hour_utc": 13,
     "rules_note": "装备测评社区，允许测评帖带链接"},
    {"name": "ScienceBasedParenting", "category": "core", "priority": 2, "best_hour_utc": 16,
     "rules_note": "需引用数据来源，禁营销"},
    # 长尾场景社区
    {"name": "WorkingMoms", "category": "longtail", "purpose": "persona", "priority": 2, "best_hour_utc": 12,
     "rules_note": "职场妈妈场景，穿戴设备话题友好"},
    {"name": "HomeSafety", "category": "longtail", "priority": 3, "best_hour_utc": 15,
     "rules_note": "居家安全话题，禁硬广"},
    {"name": "EMFSafety", "category": "longtail", "priority": 2, "best_hour_utc": 17,
     "rules_note": "低辐射需求核心场景社区"},
    {"name": "Breastfeeding", "category": "longtail", "priority": 2, "best_hour_utc": 13,
     "rules_note": "吸奶器话题社区，仅周末允许自推广", "promo_weekday": 5},
]

DEFAULT_KEYWORDS: list[dict] = [
    # SEO 适配词库
    {"keyword": "low EMF baby monitor", "category": "seo", "priority": 1},
    {"keyword": "non-wifi baby monitor", "category": "seo", "priority": 1},
    {"keyword": "wearable breast pump", "category": "seo", "priority": 1},
    {"keyword": "baby monitor without internet", "category": "seo", "priority": 2},
    {"keyword": "analog baby monitor", "category": "seo", "priority": 2},
    {"keyword": "video baby monitor no wifi", "category": "seo", "priority": 2},
    {"keyword": "hands free breast pump", "category": "seo", "priority": 2},
    {"keyword": "baby monitor radiation distance", "category": "seo", "priority": 3},
    # AI 热搜词库
    {"keyword": "best low radiation baby monitor", "category": "ai_hot", "priority": 1},
    {"keyword": "safe baby monitor for newborn", "category": "ai_hot", "priority": 1},
    {"keyword": "wearable breast pump review", "category": "ai_hot", "priority": 1},
    {"keyword": "are wifi baby monitors safe", "category": "ai_hot", "priority": 1},
    {"keyword": "lowest EMF baby monitor 2026", "category": "ai_hot", "priority": 2},
    {"keyword": "baby monitor EMF safety guide", "category": "ai_hot", "priority": 2},
    {"keyword": "best baby monitor for preemie", "category": "ai_hot", "priority": 3},
]


def seed_default_communities(db: Session, site_id: int, account_id: int | None = None) -> int:
    """预置兴趣社区模板。人设社区必须挂到 account_id；产品社区仍全站共用。"""
    persona_names = {
        c.name
        for c in db.query(RedditCommunity)
        .filter(
            RedditCommunity.site_id == site_id,
            RedditCommunity.purpose == "persona",
            RedditCommunity.account_id == account_id,
        )
        .all()
    } if account_id else set()
    promo_names = {
        c.name
        for c in db.query(RedditCommunity)
        .filter(
            RedditCommunity.site_id == site_id,
            RedditCommunity.purpose == "promo",
            RedditCommunity.account_id.is_(None),
        )
        .all()
    }
    added = 0
    for item in DEFAULT_COMMUNITIES:
        purpose = item.get("purpose", "persona")
        name = item["name"]
        if purpose == "persona":
            if not account_id or name in persona_names:
                continue
            owner = account_id
            persona_names.add(name)
        else:
            if name in promo_names:
                continue
            owner = None
            promo_names.add(name)
        db.add(
            RedditCommunity(
                site_id=site_id,
                account_id=owner,
                name=name,
                category=item.get("category", "core"),
                purpose=purpose,
                rules_note=item.get("rules_note"),
                allows_links=item.get("allows_links", True),
                promo_weekday=item.get("promo_weekday"),
                daily_post_limit=item.get("daily_post_limit", 1),
                best_hour_utc=item.get("best_hour_utc"),
                priority=item.get("priority", 3),
                is_active=True,
            )
        )
        added += 1
    db.commit()
    return added


def seed_default_keywords(db: Session, site_id: int) -> int:
    """预置 SEO + AI 热搜双词库。返回新增数量。"""
    existing = {
        (k.keyword, k.category)
        for k in db.query(RedditKeyword).filter(RedditKeyword.site_id == site_id).all()
    }
    added = 0
    for item in DEFAULT_KEYWORDS:
        key = (item["keyword"], item["category"])
        if key in existing:
            continue
        db.add(
            RedditKeyword(
                site_id=site_id,
                keyword=item["keyword"],
                category=item["category"],
                priority=item.get("priority", 3),
                used_count=0,
            )
        )
        added += 1
    db.commit()
    return added
