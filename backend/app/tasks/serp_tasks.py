"""SERP 排名抓取 Celery 任务。

任务：
1. crawl_keyword_rank(keyword_id) - 抓取单关键词排名 + 同步抓取竞品排名
2. crawl_all_keywords() - 周期任务：扫描所有 active 关键词，逐个投递
3. proxy_health_check() - 代理池健康检测
"""
from __future__ import annotations

import logging
from datetime import datetime

from app.core.celery_app import celery_app
from app.core.database import SessionLocal
from app.models.keyword import Keyword
from app.models.competitor import Competitor
from app.models.serp_rank import SerpRankSnapshot, CompetitorRankSnapshot
from app.services.serp_crawler import get_serp_crawler, SerpCrawlError, SerpBlockedError
from app.utils.rate_limiter import RateLimitExceeded, CircuitBreakerOpen
from app.utils.proxy import get_proxy_pool

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
def crawl_keyword_rank(self, keyword_id: int) -> dict:
    """抓取单个关键词的 SERP 排名 + 同步抓取竞品排名。"""
    db = SessionLocal()
    try:
        kw = db.query(Keyword).filter(Keyword.id == keyword_id).first()
        if not kw or kw.status != "active":
            return {"success": False, "error": f"keyword {keyword_id} not found/inactive"}

        # 取该用户的所有竞品
        competitors = db.query(Competitor).filter(Competitor.user_id == kw.user_id).all()
        competitor_domains = [c.domain for c in competitors]

        crawler = get_serp_crawler()
        try:
            result = crawler.crawl(
                keyword=kw.keyword,
                target_url=kw.target_url,
                region=kw.region,
                competitor_domains=competitor_domains,
            )
        except (RateLimitExceeded, CircuitBreakerOpen) as e:
            logger.warning("SERP %s 限流/熔断，将重试: %s", kw.keyword, e)
            raise

        now = datetime.utcnow()

        # 写入 serp_rank_snapshots
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

        # 写入竞品排名（P3.7）
        comp_ranks = result.serp_features.get("competitor_ranks", {})
        for comp in competitors:
            if comp.domain in comp_ranks:
                info = comp_ranks[comp.domain]
                db.add(CompetitorRankSnapshot(
                    time=now,
                    competitor_id=comp.id,
                    keyword_id=kw.id,
                    domain=comp.domain,
                    rank=info["rank"],
                    target_url=info["url"],
                ))

        db.commit()
        logger.info(
            "SERP 抓取 %s rank=%s status=%s",
            kw.keyword, result.target_rank, result.crawl_status,
        )
        return {
            "success": True,
            "keyword_id": kw.id,
            "rank": result.target_rank,
            "crawl_status": result.crawl_status,
        }
    except Exception as e:
        db.rollback()
        logger.exception("crawl_keyword_rank 失败 kw=%s: %s", keyword_id, e)
        raise
    finally:
        db.close()


@celery_app.task(name="app.tasks.serp_tasks.crawl_all_keywords")
def crawl_all_keywords() -> dict:
    """Celery Beat 每 6 小时触发：扫描所有 active 关键词，逐个投递 crawl_keyword_rank。"""
    db = SessionLocal()
    try:
        keywords = (
            db.query(Keyword)
            .filter(Keyword.status == "active")
            .order_by(Keyword.priority.asc())
            .all()
        )
        for kw in keywords:
            crawl_keyword_rank.delay(kw.id)
        logger.info("Crawl all keywords: %d tasks queued", len(keywords))
        return {"queued": len(keywords)}
    finally:
        db.close()


@celery_app.task(name="app.tasks.serp_tasks.proxy_health_check")
def proxy_health_check() -> dict:
    """代理池健康检测（Celery Beat 每 30 分钟触发）。"""
    pool = get_proxy_pool()
    if pool.is_empty:
        return {"total": 0, "healthy": 0, "unhealthy": 0, "note": "proxy pool empty (mock mode)"}
    return pool.health_check_all()
