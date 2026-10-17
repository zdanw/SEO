"""异常告警 Celery 任务。"""
from __future__ import annotations

import logging
from datetime import timedelta
from app.core.timeutil import now_bj

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
        now = now_bj()
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


@celery_app.task(name="app.tasks.alert_tasks.check_campaign_tick_heartbeat")
def check_campaign_tick_heartbeat() -> dict:
    """每 5 分钟检查 campaign_tick 心跳：调度停摆时记 WARNING（由日志采集侧触发告警）。

    可捕捉：beat 调度条目被移除、tick 持续崩溃、social 队列堵塞。
    注意本任务自身也依赖 beat 存活；beat 整体宕机需配合 /api/v1/ops/queues 外部探测。
    """
    from app.services.reddit_campaign import read_campaign_tick_heartbeat

    hb = read_campaign_tick_heartbeat()
    if not hb["stale"]:
        return {"ok": True, "age_minutes": hb["age_minutes"]}
    reason = hb["reason"]
    if reason == "missing":
        logger.warning("Campaign 调度心跳缺失：campaign_tick 从未运行或已停摆超过 2 小时")
    elif reason == "redis_unavailable":
        logger.warning("Campaign 调度心跳检查失败：Redis 不可用")
    elif reason == "corrupted":
        logger.warning("Campaign 调度心跳数据异常，无法解析时间戳")
    else:
        logger.warning(
            "Campaign 调度心跳过期：%.0f 分钟未运行（阈值 20 分钟），请检查 beat 与 social worker",
            hb["age_minutes"],
        )
    return {"ok": False, "reason": reason, "age_minutes": hb["age_minutes"]}
