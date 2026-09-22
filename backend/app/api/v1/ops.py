"""运维可观测：队列积压、任务台账、成本与配额。"""
from __future__ import annotations

from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.api.deps import SiteContext, get_site_context
from app.core.config import settings
from app.core.database import get_db
from app.models.reddit import RedditComment, RedditPost
from app.models.task_run import TaskRun
from app.services.serp_provider import get_serp_quota_remaining, get_serp_quota_used, resolve_provider_name
from app.utils.celery_redis import get_celery_queue_depth
from app.utils.rate_limiter import get_redis

router = APIRouter()

_QUEUE_ALERT_THRESHOLD = 100


@router.get("/queues", tags=["运维"])
def get_queue_health(
    ctx: SiteContext = Depends(get_site_context),
):
    """查看 Celery 队列积压（serp / social / default）。读 Celery broker Redis DB。"""
    _ = ctx
    queues = {}
    alerts = []
    for name in ("serp", "social", "default"):
        depth = get_celery_queue_depth(name)
        queues[name] = {"depth": depth, "alert": depth is not None and depth >= _QUEUE_ALERT_THRESHOLD}
        if depth is not None and depth >= _QUEUE_ALERT_THRESHOLD:
            alerts.append(f"队列 {name} 积压 {depth}（阈值 {_QUEUE_ALERT_THRESHOLD}）")
    return {
        "queues": queues,
        "alert_threshold": _QUEUE_ALERT_THRESHOLD,
        "alerts": alerts,
        "checked_at": datetime.utcnow().isoformat(),
    }


@router.get("/costs", tags=["运维"])
def get_cost_snapshot(
    db: Session = Depends(get_db),
    ctx: SiteContext = Depends(get_site_context),
):
    """当日 SERP 配额/费用估算 + 近 7 日发布成功率。"""
    site_id = ctx.site.id
    today = date.today()
    used = get_serp_quota_used(today)
    remaining = get_serp_quota_remaining(today)
    cost = 0.0
    try:
        raw = get_redis().get(f"serp:cost:{today.isoformat()}")
        cost = float(raw or 0)
    except Exception:
        cost = used * float(settings.SERP_EST_COST_PER_REQUEST)

    week_ago = datetime.utcnow() - timedelta(days=7)
    posts_total = (
        db.query(func.count(RedditPost.id))
        .filter(
            RedditPost.site_id == site_id,
            RedditPost.updated_at >= week_ago,
            RedditPost.status.in_(("posted", "failed")),
        )
        .scalar()
        or 0
    )
    posts_ok = (
        db.query(func.count(RedditPost.id))
        .filter(
            RedditPost.site_id == site_id,
            RedditPost.updated_at >= week_ago,
            RedditPost.status == "posted",
        )
        .scalar()
        or 0
    )
    comments_total = (
        db.query(func.count(RedditComment.id))
        .filter(
            RedditComment.site_id == site_id,
            RedditComment.updated_at >= week_ago,
            RedditComment.status.in_(("posted", "failed")),
        )
        .scalar()
        or 0
    )
    comments_ok = (
        db.query(func.count(RedditComment.id))
        .filter(
            RedditComment.site_id == site_id,
            RedditComment.updated_at >= week_ago,
            RedditComment.status == "posted",
        )
        .scalar()
        or 0
    )

    def _rate(ok: int, total: int) -> float | None:
        if total <= 0:
            return None
        return round(ok / total * 100, 1)

    return {
        "serp_provider": resolve_provider_name(),
        "serp_allow_proxy_fallback": settings.SERP_ALLOW_PROXY_FALLBACK,
        "serp_today": {
            "used": used,
            "remaining": remaining if settings.SERP_DAILY_QUOTA > 0 else None,
            "daily_quota": settings.SERP_DAILY_QUOTA,
            "estimated_cost_usd": round(cost, 4),
            "est_cost_per_request": settings.SERP_EST_COST_PER_REQUEST,
        },
        "publish_7d": {
            "posts_success_rate": _rate(posts_ok, posts_total),
            "posts_ok": posts_ok,
            "posts_total": posts_total,
            "comments_success_rate": _rate(comments_ok, comments_total),
            "comments_ok": comments_ok,
            "comments_total": comments_total,
        },
        "note": "费用为估算值；排名与发帖互动不做自动因果归因。",
    }


@router.get("/tasks", tags=["运维"])
def list_recent_tasks(
    business_type: str | None = None,
    business_id: int | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
    ctx: SiteContext = Depends(get_site_context),
):
    """按业务 ID 或站点查看最近任务执行记录。"""
    q = db.query(TaskRun).filter(
        (TaskRun.site_id == ctx.site.id) | (TaskRun.site_id.is_(None))
    )
    if business_type:
        q = q.filter(TaskRun.business_type == business_type)
    if business_id is not None:
        q = q.filter(TaskRun.business_id == business_id)
    rows = q.order_by(TaskRun.started_at.desc()).limit(limit).all()
    return [
        {
            "id": r.id,
            "task_name": r.task_name,
            "celery_task_id": r.celery_task_id,
            "request_id": r.request_id,
            "business_type": r.business_type,
            "business_id": r.business_id,
            "status": r.status,
            "started_at": r.started_at.isoformat() if r.started_at else None,
            "finished_at": r.finished_at.isoformat() if r.finished_at else None,
            "error_message": r.error_message,
            "retries": r.retries,
            "meta": r.meta,
        }
        for r in rows
    ]
