"""数据大屏 API：汇总卡片、排名趋势、社交漏斗。"""
from __future__ import annotations

from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy import Integer, cast, func, text
from sqlalchemy.orm import Session

from app.api.deps import SiteContext, get_site_context
from app.core.database import get_db
from app.models.keyword import Keyword
from app.models.serp_rank import SerpRankSnapshot
from app.models.social import SocialAccount, SocialPost

router = APIRouter()


def _engagement_clicks():
    return func.coalesce(
        cast(func.json_extract_path_text(SocialPost.engagement, "clicks"), Integer),
        0,
    )


def _calc_trend(current: int, previous: int) -> float | None:
    if previous == 0:
        return 100.0 if current > 0 else None
    return round((current - previous) / previous * 100, 1)


@router.get("/summary", tags=["数据大屏"])
def get_summary(
    days: int = Query(default=7, ge=1, le=365),
    db: Session = Depends(get_db),
    ctx: SiteContext = Depends(get_site_context),
):
    site_id = ctx.site.id
    start = datetime.utcnow() - timedelta(days=days)
    prev_start = datetime.utcnow() - timedelta(days=days * 2)
    prev_end = start

    active_kws = (
        db.query(Keyword.id)
        .filter(Keyword.site_id == site_id, Keyword.status == "active")
        .all()
    )
    if active_kws:
        kw_ids = [k.id for k in active_kws]
        latest_top10 = (
            db.query(func.count(SerpRankSnapshot.keyword_id))
            .filter(
                SerpRankSnapshot.keyword_id.in_(kw_ids),
                SerpRankSnapshot.time >= start,
                SerpRankSnapshot.rank.isnot(None),
                SerpRankSnapshot.rank <= 10,
            )
            .scalar() or 0
        )
        total_latest = (
            db.query(func.count(SerpRankSnapshot.keyword_id))
            .filter(
                SerpRankSnapshot.keyword_id.in_(kw_ids),
                SerpRankSnapshot.time >= start,
                SerpRankSnapshot.rank.isnot(None),
            )
            .scalar() or 0
        )
        top10_ratio = round(latest_top10 / total_latest * 100, 1) if total_latest else 0
    else:
        top10_ratio = 0

    posted_posts = (
        db.query(func.count(SocialPost.id))
        .join(SocialAccount, SocialPost.account_id == SocialAccount.id)
        .filter(
            SocialAccount.site_id == site_id,
            SocialPost.status == "posted",
            SocialPost.posted_at >= start,
        )
        .scalar() or 0
    )
    prev_posted = (
        db.query(func.count(SocialPost.id))
        .join(SocialAccount, SocialPost.account_id == SocialAccount.id)
        .filter(
            SocialAccount.site_id == site_id,
            SocialPost.status == "posted",
            SocialPost.posted_at >= prev_start,
            SocialPost.posted_at < prev_end,
        )
        .scalar() or 0
    )

    clicks = (
        db.query(func.coalesce(func.sum(_engagement_clicks()), 0))
        .join(SocialAccount, SocialPost.account_id == SocialAccount.id)
        .filter(
            SocialAccount.site_id == site_id,
            SocialPost.status == "posted",
            SocialPost.posted_at >= start,
        )
        .scalar() or 0
    )

    active_keyword_count = (
        db.query(func.count(Keyword.id))
        .filter(Keyword.site_id == site_id, Keyword.status == "active")
        .scalar() or 0
    )

    return {
        "period_days": days,
        "site": {"id": ctx.site.id, "name": ctx.site.name, "domain": ctx.site.domain},
        "cards": [
            {"label": "监控关键词数", "value": active_keyword_count, "trend": None, "color": "#409EFF"},
            {"label": "关键词 Top10 占比", "value": f"{top10_ratio}%", "trend": None, "color": "#67C23A"},
            {"label": "成功发帖数", "value": posted_posts, "trend": _calc_trend(posted_posts, prev_posted), "color": "#E6A23C"},
            {"label": "社交引流点击", "value": int(clicks), "trend": None, "color": "#F56C6C"},
        ],
    }


@router.get("/rank-trends", tags=["数据大屏"])
def get_rank_trends(
    keyword_ids: str = Query(default="", description="逗号分隔的 keyword_id，空则取最近活跃的"),
    days: int = Query(default=14, ge=1, le=365),
    db: Session = Depends(get_db),
    ctx: SiteContext = Depends(get_site_context),
):
    site_id = ctx.site.id
    start = datetime.utcnow() - timedelta(days=days)

    if keyword_ids.strip():
        ids = [int(x) for x in keyword_ids.split(",") if x.strip()]
    else:
        ids = [
            r[0]
            for r in db.query(SerpRankSnapshot.keyword_id)
            .join(Keyword, SerpRankSnapshot.keyword_id == Keyword.id)
            .filter(
                Keyword.site_id == site_id,
                SerpRankSnapshot.time >= start,
                SerpRankSnapshot.crawl_status == "success",
            )
            .group_by(SerpRankSnapshot.keyword_id)
            .order_by(func.max(SerpRankSnapshot.time).desc())
            .limit(5)
            .all()
        ]

    if not ids:
        return {}

    rows = db.execute(
        text(
            """
            SELECT keyword_id,
                   time_bucket('1 day', time) AS bucket,
                   avg(rank) AS avg_rank
            FROM serp_rank_snapshots
            WHERE keyword_id = ANY(:ids) AND time >= :start AND crawl_status = 'success'
            GROUP BY keyword_id, bucket
            ORDER BY bucket ASC
            """
        ),
        {"ids": ids, "start": start},
    ).mappings().all()

    result: dict[int, list] = {}
    for r in rows:
        result.setdefault(r["keyword_id"], []).append({
            "time": r["bucket"].isoformat(),
            "rank": round(float(r["avg_rank"]), 1) if r["avg_rank"] else None,
        })
    return result


@router.get("/social-funnel", tags=["数据大屏"])
def get_social_funnel(
    days: int = Query(default=7, ge=1, le=365),
    db: Session = Depends(get_db),
    ctx: SiteContext = Depends(get_site_context),
):
    site_id = ctx.site.id
    start = datetime.utcnow() - timedelta(days=days)

    total_posts = (
        db.query(func.count(SocialPost.id))
        .join(SocialAccount, SocialPost.account_id == SocialAccount.id)
        .filter(SocialAccount.site_id == site_id, SocialPost.created_at >= start)
        .scalar() or 0
    )

    posted_posts = (
        db.query(func.count(SocialPost.id))
        .join(SocialAccount, SocialPost.account_id == SocialAccount.id)
        .filter(
            SocialAccount.site_id == site_id,
            SocialPost.status == "posted",
            SocialPost.posted_at >= start,
        )
        .scalar() or 0
    )

    total_clicks = (
        db.query(func.coalesce(func.sum(_engagement_clicks()), 0))
        .join(SocialAccount, SocialPost.account_id == SocialAccount.id)
        .filter(
            SocialAccount.site_id == site_id,
            SocialPost.status == "posted",
            SocialPost.posted_at >= start,
        )
        .scalar() or 0
    )

    platform_clicks = (
        db.query(
            SocialAccount.platform,
            func.coalesce(func.sum(_engagement_clicks()), 0).label("clicks"),
        )
        .join(SocialPost, SocialPost.account_id == SocialAccount.id)
        .filter(
            SocialAccount.site_id == site_id,
            SocialPost.status == "posted",
            SocialPost.posted_at >= start,
        )
        .group_by(SocialAccount.platform)
        .all()
    )

    return {
        "days": days,
        "total_posts": total_posts,
        "posted_posts": posted_posts,
        "total_clicks": int(total_clicks),
        "by_platform": [{"platform": row[0], "clicks": int(row[1])} for row in platform_clicks],
    }
