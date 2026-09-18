"""异常告警 Celery 任务。"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

from sqlalchemy import func

from app.core.celery_app import celery_app
from app.core.database import SessionLocal
from app.models.serp_rank import SerpRankSnapshot

logger = logging.getLogger(__name__)


@celery_app.task(name="app.tasks.alert_tasks.check_serp_failures")
def check_serp_failures() -> dict:
    """每日执行：统计 SERP 抓取失败率。"""
    db = SessionLocal()
    try:
        now = datetime.utcnow()
        yesterday = now - timedelta(days=1)
        total = (
            db.query(func.count(SerpRankSnapshot.time))
            .filter(SerpRankSnapshot.time >= yesterday)
            .scalar()
        ) or 0
        failed = (
            db.query(func.count(SerpRankSnapshot.time))
            .filter(
                SerpRankSnapshot.time >= yesterday,
                SerpRankSnapshot.crawl_status != "success",
            )
            .scalar()
        ) or 0
        rate = failed / total if total else 0
        if rate > 0.3:
            logger.warning("SERP 抓取失败率 %.0f%%（近 24h %s/%s）", rate * 100, failed, total)
        return {"total": total, "failed": failed, "rate": rate}
    finally:
        db.close()
