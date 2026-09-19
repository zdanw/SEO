"""Reddit 自动化 Celery 任务（方案 第九章：自动化落地体系）。

- publish_scheduled_reddit_content: 定时发布（每 15 分钟扫描到期任务）
- sync_reddit_post_metrics: 帖子发布后数据自动汇总（每日）
- reddit_account_health_check: 账号健康检查与风控预警（每日）
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

from celery import shared_task
from sqlalchemy.orm import Session

from app.core.celery_app import celery_app
from app.core.database import SessionLocal
from app.models.reddit import RedditAccountProfile, RedditComment, RedditPost, RedditPostMetric
from app.services.reddit_publish import publish_comment_now, publish_post_now
from app.services.reddit_risk import (
    clear_warning,
    ensure_karma_stage_consistency,
    get_account_daily_usage,
    mark_warning,
)

logger = logging.getLogger(__name__)


@celery_app.task(name="app.tasks.reddit_tasks.publish_scheduled_reddit_content")
def publish_scheduled_reddit_content() -> dict:
    """扫描到期的定时帖子/评论并自动发布（分时段发布，避免集中触发风控）。"""
    db: Session = SessionLocal()
    published_posts = 0
    published_comments = 0
    try:
        now = datetime.utcnow()
        posts = (
            db.query(RedditPost)
            .filter(
                RedditPost.status == "approved",
                RedditPost.scheduled_at.isnot(None),
                RedditPost.scheduled_at <= now,
            )
            .limit(20)
            .all()
        )
        for post in posts:
            try:
                publish_post_now(db, post)
                published_posts += 1
            except Exception as exc:  # 单条失败不阻塞其他任务
                logger.warning("Scheduled post #%s publish failed: %s", post.id, exc)
                db.rollback()

        comments = (
            db.query(RedditComment)
            .filter(
                RedditComment.status == "approved",
                RedditComment.scheduled_at.isnot(None),
                RedditComment.scheduled_at <= now,
            )
            .limit(50)
            .all()
        )
        for comment in comments:
            try:
                publish_comment_now(db, comment)
                published_comments += 1
            except Exception as exc:
                logger.warning("Scheduled comment #%s publish failed: %s", comment.id, exc)
                db.rollback()

        if published_posts or published_comments:
            logger.info(
                "Scheduled reddit content: %d posts, %d comments published",
                published_posts,
                published_comments,
            )
        return {"published_posts": published_posts, "published_comments": published_comments}
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
                return _client.list_feed(subreddit, limit=limit)

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
