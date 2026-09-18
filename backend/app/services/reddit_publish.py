"""Reddit 发布服务：帖子 / 评论的统一发布入口（API 与 Celery 任务共用）。

发布前执行风控校验（频次/去重/养号阶段/社区规则），发布后回写状态与发布时间。
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

from app.models.reddit import RedditComment, RedditPost
from app.services.reddit_client import RedditApiError, get_reddit_client_for_account
from app.services.reddit_mix import count_mix_window, enforce_promo_quota
from app.services.reddit_risk import RiskViolation, check_comment_publish, check_post_publish


def enforce_publish_mix_quota(db: Session, site_id: int, intent: str | None) -> None:
    mix = count_mix_window(db, site_id)
    enforce_promo_quota(intent=intent or "casual", casual_count=mix.casual, promo_count=mix.promo)


def publish_post_now(db: Session, post: RedditPost, *, skip_risk_check: bool = False) -> RedditPost:
    """发布一条已批准的 Reddit 帖子。失败时置为 failed 并记录原因。"""
    if post.status == "posted":
        return post
    if post.status not in ("approved", "posting"):
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
        post.published_at = datetime.utcnow()
        post.error_message = None
        post.scheduled_at = None
    except RedditApiError as exc:
        post.status = "failed"
        post.error_message = str(exc)
    db.commit()
    db.refresh(post)
    return post


def publish_comment_now(db: Session, comment: RedditComment, *, skip_risk_check: bool = False) -> RedditComment:
    """发布一条已批准的 Reddit 评论。"""
    if comment.status == "posted":
        return comment
    if comment.status not in ("approved", "posting"):
        raise ValueError("请先批准后再发布")

    enforce_publish_mix_quota(db, comment.site_id, comment.content_intent)

    if not skip_risk_check:
        report = check_comment_publish(
            db,
            site_id=comment.site_id,
            account_id=comment.account_id,
            body=comment.body,
        )
        if not report.ok:
            raise RiskViolation(report.errors, report.warnings)

    account = comment.account
    if account is None:
        raise ValueError("关联账号不存在")

    comment.status = "posting"
    db.commit()
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
