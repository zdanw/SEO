"""异常告警 Celery 任务。

- check_serp_failures: 统计近 24h SERP 抓取失败率，过高告警
- check_backlinks_health: 触发外链存活检测
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

from sqlalchemy import func

from app.core.celery_app import celery_app
from app.core.database import SessionLocal
from app.models.serp_rank import SerpRankSnapshot
from app.models.recommendation import Recommendation
from app.services.backlink_monitor import get_backlink_monitor

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
            rec = Recommendation(
                category="performance",
                severity="warning",
                title=f"SERP 抓取失败率 {rate * 100:.0f}%",
                description=f"近 24h 抓取 {total} 次，失败 {failed} 次",
                suggestion="检查代理池健康状态；增加代理数量；降低抓取频率",
            )
            db.add(rec)
            db.commit()
        return {"total": total, "failed": failed, "rate": rate}
    finally:
        db.close()


@celery_app.task(name="app.tasks.alert_tasks.check_backlinks_health")
def check_backlinks_health(user_id: int | None = None) -> dict:
    """每周执行：触发外链存活检测。"""
    db = SessionLocal()
    try:
        monitor = get_backlink_monitor()
        if user_id:
            return monitor.check_batch(db, user_id, limit=200)
        from app.models.user import User
        users = db.query(User).all()
        total_result: dict[str, int] = {"total": 0, "alive": 0, "dead": 0}
        for u in users:
            r = monitor.check_batch(db, u.id, limit=100)
            total_result["total"] += r["total"]
            total_result["alive"] += r["alive"]
            total_result["dead"] += r["dead"]
        return total_result
    finally:
        db.close()
