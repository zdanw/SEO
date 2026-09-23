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

# 内容查重阈值：与同站点近 7 天内容相似度超过该值视为模板化重复（含跨账号）
SIMILARITY_THRESHOLD = 0.85
DEDUP_WINDOW_DAYS = 7
DEDUP_COMPARE_LIMIT = 200


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


@dataclass
class SimilarityHit:
    other_id: int
    account_id: int | None
    ratio: float
    message: str


def _norm_text(text: str) -> str:
    return " ".join((text or "").lower().split())


def _text_similarity(a: str, b: str) -> float:
    """词级 + 字符级取较高值，减轻换词不换意漏检。"""
    ta = _norm_text(a).split()
    tb = _norm_text(b).split()
    if not ta or not tb:
        return 0.0
    char_r = difflib.SequenceMatcher(None, " ".join(ta), " ".join(tb)).ratio()
    tok_r = difflib.SequenceMatcher(None, ta, tb).ratio()
    return max(char_r, tok_r)


DEDUP_STATUSES = frozenset({"posted", "posting"})


def find_similar_on_site(
    db: Session,
    *,
    model: type,
    site_id: int,
    body: str,
    exclude_id: int | None = None,
    kind_label: str = "内容",
) -> SimilarityHit | None:
    """站点级查重（矩阵号互相撞车也会拦）。仅已发布；按 published_at；最多比近 N 条。"""
    norm = _norm_text(body)
    if len(norm) < 40:
        return None
    week_ago = datetime.utcnow() - timedelta(days=DEDUP_WINDOW_DAYS)
    recent = (
        db.query(model)
        .filter(
            model.site_id == site_id,
            model.status.in_(list(DEDUP_STATUSES)),
            model.published_at.isnot(None),
            model.published_at >= week_ago,
            model.id != (exclude_id or 0),
        )
        .order_by(model.published_at.desc())
        .limit(DEDUP_COMPARE_LIMIT)
        .all()
    )
    for other in recent:
        other_body = getattr(other, "body", None) or ""
        ratio = _text_similarity(norm, other_body)
        if ratio >= SIMILARITY_THRESHOLD:
            account = getattr(other, "account_id", None)
            acc_bit = f"，账号 #{account}" if account is not None else ""
            msg = (
                f"{kind_label}与站点近期 #{other.id}{acc_bit} 相似度过高（{ratio:.0%}），"
                f"疑似矩阵撞车/模板化重复，请改写后发布"
            )
            return SimilarityHit(
                other_id=int(other.id),
                account_id=int(account) if account is not None else None,
                ratio=float(ratio),
                message=msg,
            )
    return None


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

    # 7) 内容去重：与同站点近 7 天帖子正文相似度检测（含跨账号）
    similar = find_similar_on_site(
        db,
        model=RedditPost,
        site_id=site_id,
        body=body,
        exclude_id=exclude_post_id,
        kind_label="内容",
    )
    if similar:
        errors.append(similar.message)

    return RiskReport(ok=not errors, errors=errors, warnings=warnings)


def check_comment_publish(
    db: Session,
    *,
    site_id: int,
    account_id: int,
    body: str,
    exclude_comment_id: int | None = None,
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

    similar = find_similar_on_site(
        db,
        model=RedditComment,
        site_id=site_id,
        body=body,
        exclude_id=exclude_comment_id,
        kind_label="评论",
    )
    if similar:
        errors.append(similar.message)

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
