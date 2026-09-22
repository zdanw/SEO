"""SERP 排名抓取 Celery 任务。"""
from __future__ import annotations

import logging
from datetime import datetime

from app.core.celery_app import celery_app
from app.core.config import settings
from app.core.database import SessionLocal
from app.models.keyword import Keyword
from app.models.serp_rank import SerpRankSnapshot
from app.models.task_run import finish_task_run, start_task_run
from app.services.serp_crawler import (
    SerpBlockedError,
    SerpCrawlError,
    SerpQuotaExceeded,
    get_serp_crawler,
)
from app.services.serp_provider import get_serp_quota_remaining
from app.utils.proxy import get_proxy_pool
from app.utils.rate_limiter import CircuitBreakerOpen, RateLimitExceeded

logger = logging.getLogger(__name__)

MAX_RETRIES = 3


@celery_app.task(
    name="app.tasks.serp_tasks.crawl_keyword_rank",
    bind=True,
    max_retries=MAX_RETRIES,
    default_retry_delay=300,
    autoretry_for=(SerpBlockedError, SerpCrawlError, RateLimitExceeded, CircuitBreakerOpen),
    retry_backoff=True,
    retry_backoff_max=3600,
    retry_jitter=True,
)
def crawl_keyword_rank(self, keyword_id: int, request_id: str | None = None) -> dict:
    """抓取单个关键词的 SERP 排名。"""
    db = SessionLocal()
    run = None
    try:
        kw = db.query(Keyword).filter(Keyword.id == keyword_id).first()
        if not kw or kw.status != "active":
            return {"success": False, "error": f"keyword {keyword_id} not found/inactive"}

        run = start_task_run(
            db,
            task_name="crawl_keyword_rank",
            celery_task_id=getattr(self.request, "id", None),
            request_id=request_id,
            site_id=kw.site_id,
            business_type="keyword",
            business_id=kw.id,
            retries=int(getattr(self.request, "retries", 0) or 0),
        )
        logger.info(
            "SERP crawl start kw=%s request_id=%s task_run=%s",
            kw.keyword,
            request_id,
            run.id,
        )

        crawler = get_serp_crawler()
        try:
            result = crawler.crawl(
                keyword=kw.keyword,
                target_url=kw.target_url,
                region=kw.region,
            )
        except SerpQuotaExceeded as e:
            finish_task_run(db, run, status="failed", error_message=str(e))
            return {"success": False, "error": str(e), "quota_exceeded": True}
        except (RateLimitExceeded, CircuitBreakerOpen) as e:
            logger.warning("SERP %s 限流/熔断，将重试: %s", kw.keyword, e)
            finish_task_run(db, run, status="failed", error_message=str(e))
            raise

        now = datetime.utcnow()
        snapshot = SerpRankSnapshot(
            time=now,
            keyword_id=kw.id,
            target_url=kw.target_url,
            rank=result.target_rank,
            page=result.target_page,
            serp_features=result.serp_features,
            search_region=kw.region,
            proxy_used=result.proxy_used,
            crawl_status=result.crawl_status,
            error_message=result.error_message,
        )
        db.add(snapshot)
        db.commit()
        finish_task_run(
            db,
            run,
            status="success",
            meta_update={
                "rank": result.target_rank,
                "crawl_status": result.crawl_status,
                "provider": result.provider,
                "duration_ms": result.duration_ms,
                "estimated_cost": result.estimated_cost,
            },
        )
        logger.info(
            "SERP 抓取 %s rank=%s status=%s provider=%s request_id=%s",
            kw.keyword,
            result.target_rank,
            result.crawl_status,
            result.provider,
            request_id,
        )
        return {
            "success": True,
            "keyword_id": kw.id,
            "rank": result.target_rank,
            "crawl_status": result.crawl_status,
            "provider": result.provider,
            "request_id": request_id,
        }
    except Exception as e:
        db.rollback()
        if run is not None:
            try:
                finish_task_run(db, run, status="failed", error_message=str(e))
            except Exception:
                pass
        logger.exception("crawl_keyword_rank 失败 kw=%s: %s", keyword_id, e)
        raise
    finally:
        db.close()


@celery_app.task(name="app.tasks.serp_tasks.crawl_all_keywords")
def crawl_all_keywords() -> dict:
    """周期扫描 active 关键词；按优先级投递，受日配额约束。"""
    db = SessionLocal()
    try:
        remaining = get_serp_quota_remaining()
        if remaining <= 0:
            logger.warning("Crawl all skipped: SERP daily quota exhausted")
            return {"queued": 0, "skipped_quota": True}

        keywords = (
            db.query(Keyword)
            .filter(Keyword.status == "active")
            .order_by(Keyword.priority.asc(), Keyword.id.asc())
            .all()
        )
        queued = 0
        skipped_low_priority = 0
        for kw in keywords:
            if queued >= remaining:
                break
            # 配额紧张时仅跑高优先级（priority 数值越小越高）
            if remaining - queued < 20 and kw.priority > 2:
                skipped_low_priority += 1
                continue
            crawl_keyword_rank.delay(kw.id)
            queued += 1
        logger.info(
            "Crawl all keywords: queued=%d skipped_low_priority=%d remaining_quota=%d",
            queued,
            skipped_low_priority,
            remaining,
        )
        return {
            "queued": queued,
            "skipped_low_priority": skipped_low_priority,
            "quota_remaining": remaining,
            "daily_quota": settings.SERP_DAILY_QUOTA,
        }
    finally:
        db.close()


@celery_app.task(name="app.tasks.serp_tasks.proxy_health_check")
def proxy_health_check() -> dict:
    """代理池健康检测（仅代理降级开启时有意义）。"""
    pool = get_proxy_pool()
    if pool.is_empty:
        return {"total": 0, "healthy": 0, "unhealthy": 0, "note": "proxy pool empty"}
    return pool.health_check_all()
