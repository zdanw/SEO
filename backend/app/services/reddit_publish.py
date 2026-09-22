"""Reddit 发布服务：帖子 / 评论的统一发布入口（API 与 Celery 任务共用）。

发布前执行风控校验（频次/去重/养号阶段/社区规则），发布后回写状态与发布时间。
并发安全：通过条件更新抢占 posting；已有外部 ID 时只修复状态、不再调发布 API。
"""
from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.models.reddit import RedditComment, RedditPost
from app.services.reddit_client import RedditApiError, get_reddit_client_for_account
from app.services.reddit_mix import count_mix_window, enforce_promo_quota
from app.services.reddit_risk import RiskViolation, check_comment_publish, check_post_publish

_CLAIMABLE = ("approved", "failed")


def enforce_publish_mix_quota(db: Session, site_id: int, intent: str | None) -> None:
    mix = count_mix_window(db, site_id)
    enforce_promo_quota(intent=intent or "casual", casual_count=mix.casual, promo_count=mix.promo)


def _repair_posted_post(db: Session, post: RedditPost) -> RedditPost:
    """已有 Reddit 外部 ID：只回写 posted，不调用发布 API。"""
    changed = False
    if post.status != "posted":
        post.status = "posted"
        changed = True
    if post.published_at is None:
        post.published_at = datetime.utcnow()
        changed = True
    if post.error_message is not None:
        post.error_message = None
        changed = True
    if changed:
        db.commit()
        db.refresh(post)
    return post


def _repair_posted_comment(db: Session, comment: RedditComment) -> RedditComment:
    changed = False
    if comment.status != "posted":
        comment.status = "posted"
        changed = True
    if comment.published_at is None:
        comment.published_at = datetime.utcnow()
        changed = True
    if comment.error_message is not None:
        comment.error_message = None
        changed = True
    if changed:
        db.commit()
        db.refresh(comment)
    return comment


def _claim_post(db: Session, post_id: int) -> bool:
    """原子抢占：仅 approved/failed 且无外部 ID → posting。"""
    rows = (
        db.query(RedditPost)
        .filter(
            RedditPost.id == post_id,
            RedditPost.status.in_(_CLAIMABLE),
            RedditPost.reddit_post_id.is_(None),
        )
        .update(
            {
                "status": "posting",
                "updated_at": datetime.utcnow(),
            },
            synchronize_session="fetch",
        )
    )
    db.commit()
    return rows == 1


def _claim_comment(db: Session, comment_id: int) -> bool:
    rows = (
        db.query(RedditComment)
        .filter(
            RedditComment.id == comment_id,
            RedditComment.status.in_(_CLAIMABLE),
            RedditComment.reddit_comment_id.is_(None),
        )
        .update(
            {
                "status": "posting",
                "updated_at": datetime.utcnow(),
            },
            synchronize_session="fetch",
        )
    )
    db.commit()
    return rows == 1


def publish_post_now(db: Session, post: RedditPost, *, skip_risk_check: bool = False) -> RedditPost:
    """发布一条已批准（或失败可重试）的 Reddit 帖子。"""
    db.refresh(post)

    if post.reddit_post_id:
        return _repair_posted_post(db, post)
    if post.status == "posted":
        return post
    if post.status == "posting":
        # 其他 Worker 正在发布：不重复调用外部 API
        return post
    if post.status not in _CLAIMABLE:
        raise ValueError("请先批准后再发布")

    enforce_publish_mix_quota(db, post.site_id, post.content_intent)

    if not skip_risk_check:
        report = check_post_publish(
            db,
            site_id=post.site_id,
            account_id=post.account_id,
            subreddit=post.subreddit,
            body=post.body,
            site_url=post.site_url,
            exclude_post_id=post.id,
        )
        if not report.ok:
            raise RiskViolation(report.errors, report.warnings)

    account = post.account
    if account is None:
        raise ValueError("关联账号不存在")

    if not _claim_post(db, post.id):
        db.refresh(post)
        if post.reddit_post_id:
            return _repair_posted_post(db, post)
        return post

    db.refresh(post)
    try:
        client = get_reddit_client_for_account(account)
        result = client.submit_post(post.subreddit, post.title, post.body)
        post.status = "posted"
        post.reddit_post_id = result.get("name") or result.get("id")
        permalink = result.get("permalink", "")
        if permalink and not permalink.startswith("http"):
            permalink = f"https://www.reddit.com{permalink}"
        post.reddit_permalink = permalink or None
        post.published_at = datetime.utcnow()
        post.error_message = None
        post.scheduled_at = None
    except RedditApiError as exc:
        post.status = "failed"
        post.error_message = str(exc)
    db.commit()
    db.refresh(post)
    return post


def publish_comment_now(
    db: Session, comment: RedditComment, *, skip_risk_check: bool = False
) -> RedditComment:
    """发布一条已批准（或失败可重试）的 Reddit 评论。"""
    db.refresh(comment)

    if comment.reddit_comment_id:
        return _repair_posted_comment(db, comment)
    if comment.status == "posted":
        return comment
    if comment.status == "posting":
        return comment
    if comment.status not in _CLAIMABLE:
        raise ValueError("请先批准后再发布")

    enforce_publish_mix_quota(db, comment.site_id, comment.content_intent)

    if not skip_risk_check:
        report = check_comment_publish(
            db,
            site_id=comment.site_id,
            account_id=comment.account_id,
            body=comment.body,
            exclude_comment_id=comment.id,
        )
        if not report.ok:
            raise RiskViolation(report.errors, report.warnings)

    account = comment.account
    if account is None:
        raise ValueError("关联账号不存在")

    if not _claim_comment(db, comment.id):
        db.refresh(comment)
        if comment.reddit_comment_id:
            return _repair_posted_comment(db, comment)
        return comment

    db.refresh(comment)
    try:
        client = get_reddit_client_for_account(account)
        result = client.submit_comment(comment.target_thing_id, comment.body, comment.subreddit)
        comment.status = "posted"
        comment.reddit_comment_id = result.get("name") or result.get("id")
        comment.published_at = datetime.utcnow()
        comment.error_message = None
        comment.scheduled_at = None
    except RedditApiError as exc:
        comment.status = "failed"
        comment.error_message = str(exc)
    db.commit()
    db.refresh(comment)
    return comment


_DEFAULT_STALE_MINUTES = 15
_STALE_MSG = "发布卡住超时，已自动标记失败，可重试"


def force_fail_post(
    db: Session,
    post: RedditPost,
    *,
    reason: str = "手动标记失败，可重试",
) -> RedditPost:
    """将卡住的 posting（无外部 ID）强制标为 failed，以便重试。"""
    db.refresh(post)
    if post.reddit_post_id:
        raise ValueError("已有外部发布 ID，请刷新状态而非强制失败")
    if post.status != "posting":
        raise ValueError("仅发布中（posting）的任务可强制失败")
    post.status = "failed"
    post.error_message = reason
    post.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(post)
    return post


def force_fail_comment(
    db: Session,
    comment: RedditComment,
    *,
    reason: str = "手动标记失败，可重试",
) -> RedditComment:
    db.refresh(comment)
    if comment.reddit_comment_id:
        raise ValueError("已有外部发布 ID，请刷新状态而非强制失败")
    if comment.status != "posting":
        raise ValueError("仅发布中（posting）的任务可强制失败")
    comment.status = "failed"
    comment.error_message = reason
    comment.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(comment)
    return comment


def recover_stale_posting(db: Session, *, timeout_minutes: int = _DEFAULT_STALE_MINUTES) -> int:
    """将超时仍无外部 ID 的 posting 回收为 failed。返回回收条数。"""
    cutoff = datetime.utcnow() - timedelta(minutes=timeout_minutes)
    recovered = 0

    posts = (
        db.query(RedditPost)
        .filter(
            RedditPost.status == "posting",
            RedditPost.reddit_post_id.is_(None),
            RedditPost.updated_at <= cutoff,
        )
        .all()
    )
    for post in posts:
        post.status = "failed"
        post.error_message = _STALE_MSG
        post.updated_at = datetime.utcnow()
        recovered += 1

    comments = (
        db.query(RedditComment)
        .filter(
            RedditComment.status == "posting",
            RedditComment.reddit_comment_id.is_(None),
            RedditComment.updated_at <= cutoff,
        )
        .all()
    )
    for comment in comments:
        comment.status = "failed"
        comment.error_message = _STALE_MSG
        comment.updated_at = datetime.utcnow()
        recovered += 1

    if recovered:
        db.commit()
    return recovered
