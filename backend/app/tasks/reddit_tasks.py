"""Reddit 自动化 Celery 任务（方案 第九章：自动化落地体系）。

- publish_scheduled_reddit_content: 扫描到期任务并按 ID 去重入队
- publish_reddit_post_by_id / publish_reddit_comment_by_id: 单条发布（含软超时）
- sync_reddit_post_metrics: 帖子发布后数据自动汇总（每日）
- reddit_account_health_check: 账号健康检查与风控预警（每日）
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

from celery.exceptions import SoftTimeLimitExceeded
from sqlalchemy.orm import Session

from app.core.celery_app import celery_app
from app.core.database import SessionLocal
from app.models.reddit import RedditAccountProfile, RedditComment, RedditPost, RedditPostMetric
from app.services.reddit_publish import (
    publish_comment_now,
    publish_post_now,
    recover_stale_posting,
)
from app.services.reddit_risk import (
    clear_warning,
    ensure_karma_stage_consistency,
    get_account_daily_usage,
    mark_warning,
)
from app.utils.rate_limiter import get_redis

logger = logging.getLogger(__name__)

# Beat 每 15 分钟扫一次；锁 TTL 略短，避免永久占坑
_ENQUEUE_TTL_SEC = 14 * 60
_PUBLISH_SOFT_LIMIT = 55
_PUBLISH_HARD_LIMIT = 70


def try_enqueue_publish(kind: str, item_id: int, task) -> bool:
    """按业务 ID 去重入队。已有未过期锁则跳过，避免队列堆同 ID 任务。"""
    r = get_redis()
    key = f"enqueue:reddit:publish:{kind}:{item_id}"
    if not r.set(key, "1", nx=True, ex=_ENQUEUE_TTL_SEC):
        return False
    try:
        task.apply_async(
            args=[item_id],
            task_id=f"reddit-publish-{kind}-{item_id}",
        )
        return True
    except Exception:
        r.delete(key)
        raise


def _clear_enqueue_lock(kind: str, item_id: int) -> None:
    try:
        get_redis().delete(f"enqueue:reddit:publish:{kind}:{item_id}")
    except Exception:
        logger.debug("clear enqueue lock failed kind=%s id=%s", kind, item_id, exc_info=True)


def _mark_post_timeout(db: Session, post_id: int) -> None:
    post = db.query(RedditPost).filter(RedditPost.id == post_id).first()
    if post and post.status == "posting" and not post.reddit_post_id:
        post.status = "failed"
        post.error_message = "发布超时，请重试"
        db.commit()


def _mark_comment_timeout(db: Session, comment_id: int) -> None:
    comment = db.query(RedditComment).filter(RedditComment.id == comment_id).first()
    if comment and comment.status == "posting" and not comment.reddit_comment_id:
        comment.status = "failed"
        comment.error_message = "发布超时，请重试"
        db.commit()


@celery_app.task(
    name="app.tasks.reddit_tasks.publish_reddit_post_by_id",
    soft_time_limit=_PUBLISH_SOFT_LIMIT,
    time_limit=_PUBLISH_HARD_LIMIT,
)
def publish_reddit_post_by_id(post_id: int) -> dict:
    """单条帖子发布；软超时后记 failed，不清空正文。"""
    db: Session = SessionLocal()
    run = None
    try:
        from app.models.task_run import finish_task_run, start_task_run

        post = db.query(RedditPost).filter(RedditPost.id == post_id).first()
        if not post:
            return {"ok": False, "reason": "missing"}
        run = start_task_run(
            db,
            task_name="publish_reddit_post_by_id",
            business_type="reddit_post",
            business_id=post_id,
            site_id=post.site_id,
        )
        try:
            publish_post_now(db, post)
        except SoftTimeLimitExceeded:
            db.rollback()
            _mark_post_timeout(db, post_id)
            finish_task_run(db, run, status="failed", error_message="发布超时")
            logger.warning("Publish post #%s soft-timeout", post_id)
            return {"ok": False, "reason": "timeout"}
        except Exception as exc:
            logger.warning("Publish post #%s failed: %s", post_id, exc)
            db.rollback()
            finish_task_run(db, run, status="failed", error_message=str(exc))
            return {"ok": False, "reason": str(exc)}
        db.refresh(post)
        finish_task_run(db, run, status="success", meta_update={"status": post.status})
        return {"ok": True, "status": post.status, "post_id": post_id}
    finally:
        _clear_enqueue_lock("post", post_id)
        db.close()


@celery_app.task(
    name="app.tasks.reddit_tasks.publish_reddit_comment_by_id",
    soft_time_limit=_PUBLISH_SOFT_LIMIT,
    time_limit=_PUBLISH_HARD_LIMIT,
)
def publish_reddit_comment_by_id(comment_id: int) -> dict:
    """单条评论发布；软超时后记 failed。"""
    db: Session = SessionLocal()
    run = None
    try:
        from app.models.task_run import finish_task_run, start_task_run

        comment = db.query(RedditComment).filter(RedditComment.id == comment_id).first()
        if not comment:
            return {"ok": False, "reason": "missing"}
        run = start_task_run(
            db,
            task_name="publish_reddit_comment_by_id",
            business_type="reddit_comment",
            business_id=comment_id,
            site_id=comment.site_id,
        )
        try:
            publish_comment_now(db, comment)
        except SoftTimeLimitExceeded:
            db.rollback()
            _mark_comment_timeout(db, comment_id)
            finish_task_run(db, run, status="failed", error_message="发布超时")
            logger.warning("Publish comment #%s soft-timeout", comment_id)
            return {"ok": False, "reason": "timeout"}
        except Exception as exc:
            logger.warning("Publish comment #%s failed: %s", comment_id, exc)
            db.rollback()
            finish_task_run(db, run, status="failed", error_message=str(exc))
            return {"ok": False, "reason": str(exc)}
        db.refresh(comment)
        finish_task_run(db, run, status="success", meta_update={"status": comment.status})
        return {"ok": True, "status": comment.status, "comment_id": comment_id}
    finally:
        _clear_enqueue_lock("comment", comment_id)
        db.close()


@celery_app.task(name="app.tasks.reddit_tasks.recover_stale_reddit_posting")
def recover_stale_reddit_posting() -> dict:
    """回收超时仍无外部 ID 的 posting → failed。"""
    db: Session = SessionLocal()
    try:
        recovered = recover_stale_posting(db, timeout_minutes=15)
        if recovered:
            logger.info("Recovered %d stale reddit posting records", recovered)
        return {"recovered": recovered}
    finally:
        db.close()


@celery_app.task(name="app.tasks.reddit_tasks.publish_scheduled_reddit_content")
def publish_scheduled_reddit_content() -> dict:
    """扫描到期的定时帖子/评论，按 ID 去重后入队（不在本任务内同步发布）。"""
    db: Session = SessionLocal()
    queued_posts = 0
    queued_comments = 0
    skipped_posts = 0
    skipped_comments = 0
    try:
        now = datetime.utcnow()
        posts = (
            db.query(RedditPost.id)
            .filter(
                RedditPost.status == "approved",
                RedditPost.scheduled_at.isnot(None),
                RedditPost.scheduled_at <= now,
            )
            .limit(20)
            .all()
        )
        for (post_id,) in posts:
            if try_enqueue_publish("post", post_id, publish_reddit_post_by_id):
                queued_posts += 1
            else:
                skipped_posts += 1

        comments = (
            db.query(RedditComment.id)
            .filter(
                RedditComment.status == "approved",
                RedditComment.scheduled_at.isnot(None),
                RedditComment.scheduled_at <= now,
            )
            .limit(50)
            .all()
        )
        for (comment_id,) in comments:
            if try_enqueue_publish("comment", comment_id, publish_reddit_comment_by_id):
                queued_comments += 1
            else:
                skipped_comments += 1

        if queued_posts or queued_comments or skipped_posts or skipped_comments:
            logger.info(
                "Scheduled reddit enqueue: posts=%d skip=%d comments=%d skip=%d",
                queued_posts,
                skipped_posts,
                queued_comments,
                skipped_comments,
            )
        return {
            "queued_posts": queued_posts,
            "queued_comments": queued_comments,
            "skipped_posts": skipped_posts,
            "skipped_comments": skipped_comments,
        }
    finally:
        db.close()


@celery_app.task(name="app.tasks.reddit_tasks.sync_reddit_post_metrics")
def sync_reddit_post_metrics(days: int = 30) -> dict:
    """同步近 N 天已发布帖子的互动数据（score / 评论数），沉淀爆款模板与 KPI 依据。"""
    db: Session = SessionLocal()
    synced = 0
    try:
        cutoff = datetime.utcnow() - timedelta(days=days)
        posts = (
            db.query(RedditPost)
            .filter(
                RedditPost.status == "posted",
                RedditPost.published_at.isnot(None),
                RedditPost.published_at >= cutoff,
            )
            .limit(200)
            .all()
        )
        for post in posts:
            if not post.reddit_post_id:
                continue
            try:
                from app.services.reddit_client import get_reddit_client_for_account

                client = get_reddit_client_for_account(post.account)
                items = client.search_posts(post.subreddit, post.title[:80], limit=10)
                match = None
                short_id = post.reddit_post_id.removeprefix("t3_")
                for item in items:
                    tid = str(item.get("thing_id") or "")
                    if tid == post.reddit_post_id or tid.endswith(short_id):
                        match = item
                        break
                if not match:
                    continue
                db.add(
                    RedditPostMetric(
                        post_id=post.id,
                        score=int(match.get("score", 0)),
                        num_comments=int(match.get("num_comments", 0)),
                        synced_at=datetime.utcnow(),
                    )
                )
                synced += 1
            except Exception as exc:
                logger.warning("Metric sync failed for post #%s: %s", post.id, exc)
                db.rollback()
        db.commit()
        logger.info("Reddit post metrics synced: %d posts", synced)
        return {"synced": synced}
    finally:
        db.close()


@celery_app.task(name="app.tasks.reddit_tasks.reddit_account_health_check")
def reddit_account_health_check(min_posts: int = 3) -> dict:
    """账号健康检查（方案 第九章-4 风控预警自动化）：

    - 近 7 天发布 >= min_posts 且平均分 < 1 → 预警并暂停营销（切换养号模式）
    - 无异常的历史预警账号自动解除
    - 按 Karma 推进养号阶段
    """
    db: Session = SessionLocal()
    warned = 0
    recovered = 0
    try:
        week_ago = datetime.utcnow() - timedelta(days=7)
        profiles = db.query(RedditAccountProfile).all()
        for profile in profiles:
            usage = get_account_daily_usage(db, profile.account_id)
            ensure_karma_stage_consistency(db, profile)

            recent_posts = (
                db.query(RedditPost)
                .filter(
                    RedditPost.account_id == profile.account_id,
                    RedditPost.status == "posted",
                    RedditPost.published_at.isnot(None),
                    RedditPost.published_at >= week_ago,
                )
                .all()
            )
            latest_scores: dict[int, int] = {}
            if recent_posts:
                metrics = (
                    db.query(RedditPostMetric)
                    .filter(RedditPostMetric.post_id.in_([p.id for p in recent_posts]))
                    .order_by(RedditPostMetric.synced_at.asc())
                    .all()
                )
                for m in metrics:
                    latest_scores[m.post_id] = m.score

            avg_score = (
                sum(latest_scores.values()) / len(latest_scores) if latest_scores else None
            )
            low_engagement = (
                len(recent_posts) >= min_posts and avg_score is not None and avg_score < 1
            )

            if low_engagement and profile.risk_status != "warning":
                mark_warning(
                    db,
                    profile,
                    f"近 7 天发布 {len(recent_posts)} 篇平均分仅 {avg_score:.1f}，疑似限流/低互动，已切至养号模式",
                )
                warned += 1
            elif not low_engagement and profile.risk_status == "warning":
                clear_warning(db, profile)
                recovered += 1

            # 预警账号当日仍有超限营销动作时提示（日志级别即可）
            if profile.risk_status == "warning" and (usage["posts"] or usage["comments"]):
                logger.warning(
                    "Account #%s in warning state still has activity today: %s",
                    profile.account_id,
                    usage,
                )
        db.commit()
        logger.info("Reddit health check: %d warned, %d recovered", warned, recovered)
        return {"warned": warned, "recovered": recovered}
    finally:
        db.close()


@celery_app.task(name="app.tasks.reddit_tasks.discover_reddit_discussions")
def discover_reddit_discussions() -> dict:
    """按人设/产品双池扫描讨论并写入待审评论（不自动发布）。"""
    from types import SimpleNamespace

    from fastapi import HTTPException

    from app.api.v1.reddit import _generate_comment_for_url, _remaining_comment_slots
    from app.models.client_site import ClientSite
    from app.models.social import SocialAccount
    from app.services.reddit_client import get_reddit_client_for_account
    from app.services.reddit_discover import smart_discover_for_account

    db: Session = SessionLocal()
    queued_total = 0
    try:
        accounts = (
            db.query(SocialAccount)
            .filter(SocialAccount.platform == "reddit", SocialAccount.is_active.is_(True))
            .all()
        )
        for account in accounts:
            site = db.query(ClientSite).filter(ClientSite.id == account.site_id).first()
            if not site:
                continue
            remaining = min(3, _remaining_comment_slots(db, site.id, account.id))
            if remaining <= 0:
                continue
            ctx = SimpleNamespace(site=site)
            client = get_reddit_client_for_account(account)
            from app.models.reddit import RedditBrand, RedditProduct

            brand = (
                db.query(RedditBrand)
                .filter(RedditBrand.site_id == site.id, RedditBrand.is_active.is_(True))
                .order_by(RedditBrand.id.asc())
                .first()
            )
            product = None
            if brand:
                candidates = (
                    db.query(RedditProduct)
                    .filter(RedditProduct.brand_id == brand.id, RedditProduct.is_active.is_(True))
                    .order_by(RedditProduct.id.asc())
                    .all()
                )
                product = next((p for p in candidates if p.communities), None) or (
                    candidates[0] if candidates else None
                )
            brand_id = brand.id if brand else None
            product_id = product.id if product else None

            def _search(subreddit: str, keyword: str, limit: int, _client=client):
                return _client.search_posts(subreddit, keyword, limit=limit)

            def _generate(item: dict, intent: str, _account=account, _ctx=ctx) -> None:
                try:
                    _generate_comment_for_url(
                        db,
                        _ctx,
                        _account.id,
                        item["url"],
                        include_site_url=False,
                        discover_source="auto_discover",
                        keyword=item.get("title") or "",
                        known_title=item.get("title"),
                        known_body=item.get("body"),
                        content_intent=intent,
                        brand_id=brand_id,
                        product_id=product_id,
                    )
                except HTTPException as exc:
                    raise RuntimeError(getattr(exc, "detail", str(exc))) from exc

            result = smart_discover_for_account(
                db,
                site_id=site.id,
                account_id=account.id,
                generate_comment=_generate,
                search_posts=_search,
                remaining_slots=remaining,
                seed=account.id,
                product_id=product_id,
            )
            queued_total += int(result.get("queued") or 0)
        logger.info("Reddit smart discover queued %s comments", queued_total)
        return {"queued": queued_total}
    finally:
        db.close()
