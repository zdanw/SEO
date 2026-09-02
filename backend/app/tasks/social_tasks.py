"""社交发帖 Celery 异步任务。

- send_post_task: 单条发帖任务（含重试 + 指数退避）
- scan_pending_posts: 周期扫描漏发的 pending/scheduled 任务（兜底）
"""
from datetime import datetime
import logging

from celery import shared_task
from sqlalchemy.orm import Session

from app.core.celery_app import celery_app
from app.core.database import SessionLocal
from app.models.social import SocialPost, SocialAccount
from app.services.pulseforge_client import (
    get_platform_client, PublishPayload, PlatformError,
    PlatformRateLimited, PlatformCircuitOpen,
)

logger = logging.getLogger(__name__)

MAX_RETRIES = 3


@celery_app.task(
    name="app.tasks.social_tasks.send_post_task",
    bind=True,
    max_retries=MAX_RETRIES,
    default_retry_delay=60,  # 首次重试等 60s
    autoretry_for=(PlatformRateLimited, PlatformCircuitOpen),
    retry_backoff=True,        # 指数退避：60s, 120s, 240s
    retry_backoff_max=1800,    # 最多 30 分钟
    retry_jitter=True,         # 加随机抖动避免雪崩
)
def send_post_task(self, post_id: int) -> dict:
    """发送一条社交帖子。

    失败重试策略：
    - 限流 / 熔断：自动重试（指数退避）
    - 其他错误：标记 failed，不重试
    """
    db: Session = SessionLocal()
    try:
        post = db.query(SocialPost).filter(SocialPost.id == post_id).first()
        if not post:
            return {"success": False, "error": f"post {post_id} not found"}

        if post.status in ("posted", "cancelled"):
            return {"success": True, "skipped": True, "reason": f"status={post.status}"}

        account = db.query(SocialAccount).filter(SocialAccount.id == post.account_id).first()
        if not account or not account.is_active:
            post.status = "failed"
            post.error_message = "账号不存在或已禁用"
            db.commit()
            return {"success": False, "error": "account inactive"}

        # 调用平台客户端
        client = get_platform_client(
            platform=account.platform,
            account_id=account.id,
            access_token=account.access_token,
            config=account.config,
        )
        payload = PublishPayload(
            title=post.title,
            summary=post.summary or "",
            image_url=post.image_url,
            external_url=post.external_url,
            hashtags=post.hashtags or [],
        )

        post.status = "posting"
        db.commit()

        result = client.publish_post(payload)

        if result.success:
            post.status = "posted"
            post.platform_post_id = result.platform_post_id
            post.posted_at = datetime.utcnow()
            post.error_message = None
            db.commit()
            logger.info("Post #%s sent to %s, platform_id=%s", post_id, account.platform, result.platform_post_id)
            return {"success": True, "platform_post_id": result.platform_post_id}
        else:
            post.status = "failed"
            post.error_message = result.error or "平台返回失败"
            post.retry_count += 1
            db.commit()
            return {"success": False, "error": result.error}

    except (PlatformRateLimited, PlatformCircuitOpen) as e:
        # 这些异常会被 Celery 自动重试
        db.rollback()
        post = db.query(SocialPost).filter(SocialPost.id == post_id).first()
        if post:
            post.retry_count += 1
            post.error_message = f"重试中: {e}"
            db.commit()
        logger.warning("Post #%s rate-limited/breaker-open, will retry: %s", post_id, e)
        raise  # 交给 Celery 重试

    except PlatformError as e:
        db.rollback()
        post = db.query(SocialPost).filter(SocialPost.id == post_id).first()
        if post:
            post.status = "failed"
            post.error_message = str(e)
            post.retry_count += 1
            db.commit()
        logger.error("Post #%s failed permanently: %s", post_id, e)
        return {"success": False, "error": str(e)}

    except Exception as e:
        db.rollback()
        logger.exception("Post #%s unexpected error: %s", post_id, e)
        post = db.query(SocialPost).filter(SocialPost.id == post_id).first()
        if post:
            post.status = "failed"
            post.error_message = f"未知错误: {e}"
            db.commit()
        return {"success": False, "error": str(e)}

    finally:
        db.close()


@celery_app.task(name="app.tasks.social_tasks.scan_pending_posts")
def scan_pending_posts() -> dict:
    """周期任务（Celery Beat 每 10 分钟）：扫描所有 pending/scheduled 且已过 eta 的任务，补发。

    用途：防止 Celery eta 任务因 worker 重启等原因丢失。
    """
    db: Session = SessionLocal()
    try:
        now = datetime.utcnow()
        posts = (
            db.query(SocialPost)
            .filter(
                SocialPost.status.in_(["pending", "scheduled"]),
                SocialPost.scheduled_at.isnot(None),
                SocialPost.scheduled_at <= now,
            )
            .limit(100)
            .all()
        )
        count = 0
        for post in posts:
            # 异步投递，不阻塞
            send_post_task.delay(post.id)
            count += 1
        logger.info("Scan pending posts: %d tasks re-queued", count)
        return {"requeued": count}
    finally:
        db.close()
