"""SERP 排名查询 API。"""
from __future__ import annotations

from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.api.deps import SiteContext, get_current_user, get_site_context, require_site_write
from app.core.config import settings
from app.core.database import get_db
from app.models.keyword import Keyword
from app.models.serp_rank import SerpRankSnapshot
from app.models.user import User
from app.schemas.monitor import LatestRankOut, SerpRankSnapshotOut
from app.services.serp_crawler import get_serp_crawler
from app.services.serp_provider import get_serp_quota_remaining, get_serp_quota_used
from app.tasks.serp_tasks import crawl_keyword_rank

router = APIRouter()


@router.get("/config")
def get_serp_config(
    current_user: User = Depends(get_current_user),
):
    _ = current_user
    crawler = get_serp_crawler()
    if crawler.is_scrapingbee:
        mode = "scrapingbee"
    elif crawler.is_proxy:
        mode = "proxy"
    else:
        mode = "mock"
    return {
        "mode": mode,
        "allow_proxy_fallback": settings.SERP_ALLOW_PROXY_FALLBACK,
        "daily_quota": settings.SERP_DAILY_QUOTA,
        "quota_used_today": get_serp_quota_used(),
        "quota_remaining_today": get_serp_quota_remaining()
        if settings.SERP_DAILY_QUOTA > 0
        else None,
        "note": "默认优先合规 SERP API；代理直抓需 SERP_ALLOW_PROXY_FALLBACK=true。",
    }


@router.get("/snapshots", response_model=list[SerpRankSnapshotOut])
def list_snapshots(
    keyword_id: int | None = None,
    crawl_status: str | None = None,
    page: int = Query(default=1, ge=1),
    size: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
    ctx: SiteContext = Depends(get_site_context),
):
    q = (
        db.query(SerpRankSnapshot)
        .join(Keyword, SerpRankSnapshot.keyword_id == Keyword.id)
        .filter(Keyword.site_id == ctx.site.id)
    )
    if keyword_id:
        q = q.filter(SerpRankSnapshot.keyword_id == keyword_id)
    if crawl_status:
        q = q.filter(SerpRankSnapshot.crawl_status == crawl_status)
    return q.order_by(SerpRankSnapshot.time.desc()).offset((page - 1) * size).limit(size).all()


@router.get("/trends")
def get_trends(
    keyword_ids: str = Query(..., description="逗号分隔的 keyword_id"),
    days: int = Query(default=30, ge=1, le=365),
    db: Session = Depends(get_db),
    ctx: SiteContext = Depends(get_site_context),
):
    ids = [int(x) for x in keyword_ids.split(",") if x.strip()]
    start = datetime.utcnow() - timedelta(days=days)
    valid_ids = {
        k.id
        for k in db.query(Keyword.id)
        .filter(Keyword.id.in_(ids), Keyword.site_id == ctx.site.id)
        .all()
    }
    if not valid_ids:
        return {}

    sql = text(
        """
        SELECT keyword_id,
               time_bucket('6 hours', time) AS bucket,
               avg(rank) AS avg_rank,
               max(crawl_status) AS last_status
        FROM serp_rank_snapshots
        WHERE keyword_id = ANY(:ids)
          AND time >= :start
          AND crawl_status = 'success'
          AND rank IS NOT NULL
        GROUP BY keyword_id, bucket
        ORDER BY bucket ASC
        """
    )
    rows = db.execute(sql, {"ids": list(valid_ids), "start": start}).mappings().all()
    result: dict[int, list] = {}
    for r in rows:
        result.setdefault(r["keyword_id"], []).append(
            {
                "time": r["bucket"].isoformat(),
                "rank": float(r["avg_rank"]) if r["avg_rank"] else None,
                "crawl_status": r["last_status"],
            }
        )
    return result


@router.get("/latest", response_model=list[LatestRankOut])
def get_latest_ranks(
    db: Session = Depends(get_db),
    ctx: SiteContext = Depends(get_site_context),
):
    keywords = (
        db.query(Keyword)
        .filter(Keyword.site_id == ctx.site.id, Keyword.status == "active")
        .order_by(Keyword.priority.asc())
        .all()
    )
    result: list[dict] = []
    for kw in keywords:
        snap = (
            db.query(SerpRankSnapshot)
            .filter(SerpRankSnapshot.keyword_id == kw.id)
            .order_by(SerpRankSnapshot.time.desc())
            .first()
        )
        result.append(
            {
                "keyword_id": kw.id,
                "keyword": kw.keyword,
                "target_url": kw.target_url,
                "rank": snap.rank if snap else None,
                "crawl_status": snap.crawl_status if snap else "pending",
                "last_crawled": snap.time.isoformat() if snap else None,
            }
        )
    return result


@router.post("/crawl/{keyword_id}")
def trigger_crawl(
    keyword_id: int,
    request: Request,
    db: Session = Depends(get_db),
    ctx: SiteContext = Depends(require_site_write),
):
    kw = (
        db.query(Keyword)
        .filter(Keyword.id == keyword_id, Keyword.site_id == ctx.site.id)
        .first()
    )
    if not kw:
        raise HTTPException(404, "关键词不存在")
    request_id = getattr(request.state, "request_id", None)
    task = crawl_keyword_rank.delay(keyword_id, request_id=request_id)
    return {"task_id": task.id, "keyword": kw.keyword, "request_id": request_id}
