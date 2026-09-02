"""数据大屏 API：汇总卡片、排名趋势、社交漏斗、外链统计 + 推荐工单 CRUD。"""
from __future__ import annotations

from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import Integer, cast, func, text
from sqlalchemy.orm import Session

from app.api.deps import SiteContext, get_site_context, require_site_write
from app.core.database import get_db
from app.models.article import Article
from app.models.backlink import Backlink
from app.models.keyword import Keyword
from app.models.recommendation import Recommendation
from app.models.serp_rank import SerpRankSnapshot
from app.models.social import SocialAccount, SocialPost
from app.services.recommender import (
    generate_recommendations,
    get_open_recommendations,
    update_recommendation_status,
)

router = APIRouter()


def _engagement_clicks():
    return func.coalesce(
        cast(func.json_extract_path_text(SocialPost.engagement, "clicks"), Integer),
        0,
    )


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

    new_articles = (
        db.query(func.count(Article.id))
        .filter(Article.site_id == site_id, Article.published_at >= start)
        .scalar() or 0
    )
    prev_articles = (
        db.query(func.count(Article.id))
        .filter(
            Article.site_id == site_id,
            Article.published_at >= prev_start,
            Article.published_at < prev_end,
        )
        .scalar() or 0
    )
    articles_trend = _calc_trend(new_articles, prev_articles)

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

    bl_new = (
        db.query(func.count(Backlink.id))
        .filter(Backlink.site_id == site_id, Backlink.first_seen_at >= start)
        .scalar() or 0
    )
    bl_lost = (
        db.query(func.count(Backlink.id))
        .filter(
            Backlink.site_id == site_id,
            Backlink.lost_at.isnot(None),
            Backlink.lost_at >= start,
        )
        .scalar() or 0
    )
    bl_net = bl_new - bl_lost

    pending_recs = (
        db.query(func.count(Recommendation.id))
        .filter(Recommendation.site_id == site_id, Recommendation.status == "open")
        .scalar() or 0
    )

    return {
        "period_days": days,
        "site": {"id": ctx.site.id, "name": ctx.site.name, "domain": ctx.site.domain},
        "cards": [
            {"label": "本周新发布文章", "value": new_articles, "trend": articles_trend, "color": "#409EFF"},
            {"label": "关键词 Top10 占比", "value": f"{top10_ratio}%", "trend": None, "color": "#67C23A"},
            {"label": "社交引流点击", "value": clicks, "trend": None, "color": "#E6A23C"},
            {"label": "监控关键词数", "value": active_keyword_count, "trend": None, "color": "#F56C6C"},
            {"label": "外链净增", "value": bl_net, "trend": None, "color": "#909399"},
            {"label": "待处理建议数", "value": pending_recs, "trend": None, "color": "#FFF566" if pending_recs > 0 else "#67C23A"},
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

    published_articles = (
        db.query(func.count(Article.id))
        .filter(
            Article.site_id == site_id,
            Article.status == "published",
            Article.published_at >= start,
        )
        .scalar() or 0
    )

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
        "articles_published": published_articles,
        "total_posts": total_posts,
        "posted_posts": posted_posts,
        "total_clicks": int(total_clicks),
        "by_platform": [{"platform": row[0], "clicks": int(row[1])} for row in platform_clicks],
    }


@router.get("/backlink-stats", tags=["数据大屏"])
def get_backlink_stats(
    days: int = Query(default=30, ge=1, le=365),
    db: Session = Depends(get_db),
    ctx: SiteContext = Depends(get_site_context),
):
    site_id = ctx.site.id
    start = datetime.utcnow() - timedelta(days=days)

    total = (
        db.query(func.count(Backlink.id))
        .filter(Backlink.site_id == site_id)
        .scalar() or 0
    )
    alive = (
        db.query(func.count(Backlink.id))
        .filter(Backlink.site_id == site_id, Backlink.is_alive == True)
        .scalar() or 0
    )
    new_in_period = (
        db.query(func.count(Backlink.id))
        .filter(Backlink.site_id == site_id, Backlink.first_seen_at >= start)
        .scalar() or 0
    )
    lost_in_period = (
        db.query(func.count(Backlink.id))
        .filter(
            Backlink.site_id == site_id,
            Backlink.lost_at.isnot(None),
            Backlink.lost_at >= start,
        )
        .scalar() or 0
    )

    return {
        "period_days": days,
        "total_backlinks": total,
        "alive_backlinks": alive,
        "new_this_period": new_in_period,
        "lost_this_period": lost_in_period,
        "net_change": new_in_period - lost_in_period,
        "loss_rate": round(lost_in_period / new_in_period * 100, 1) if new_in_period else 0,
        "alive_rate": round(alive / total * 100, 1) if total else 0,
    }


@router.get("/recommendations", tags=["数据大屏"])
def list_recommendations(
    category: str | None = Query(default=None),
    severity: str | None = Query(default=None),
    status: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
    ctx: SiteContext = Depends(get_site_context),
):
    records, total = get_open_recommendations(
        db, ctx.site.id,
        category=category, severity=severity, status=status,
        page=page, size=size,
    )
    return {
        "total": total,
        "page": page,
        "size": size,
        "items": [
            {
                "id": r.id,
                "user_id": r.user_id,
                "site_id": r.site_id,
                "article_id": r.article_id,
                "keyword_id": r.keyword_id,
                "category": r.category,
                "severity": r.severity,
                "title": r.title,
                "description": r.description,
                "suggestion": r.suggestion,
                "status": r.status,
                "created_at": r.created_at.isoformat() if r.created_at else None,
                "resolved_at": r.resolved_at.isoformat() if r.resolved_at else None,
            }
            for r in records
        ],
    }


@router.post("/recommendations/generate", tags=["数据大屏"])
def run_recommender(
    days_lookback: int = Query(default=7, ge=1, le=365),
    db: Session = Depends(get_db),
    ctx: SiteContext = Depends(require_site_write),
):
    created = generate_recommendations(db, ctx.site.id, ctx.member.user_id, days_lookback=days_lookback)
    return {"created": len(created), "ids": [r.id for r in created]}


@router.patch("/recommendations/{rec_id}", tags=["数据大屏"])
def patch_recommendation(
    rec_id: int,
    payload: dict,
    db: Session = Depends(get_db),
    ctx: SiteContext = Depends(require_site_write),
):
    status_val = payload.get("status")
    if not status_val:
        raise HTTPException(status_code=400, detail="必须提供 status 字段")
    if status_val not in ("open", "in_progress", "resolved", "ignored"):
        raise HTTPException(status_code=400, detail="status 值不合法")

    rec = update_recommendation_status(db, rec_id, ctx.site.id, status_val)
    if not rec:
        raise HTTPException(status_code=404, detail="工单不存在")
    return {
        "id": rec.id,
        "status": rec.status,
        "resolved_at": rec.resolved_at.isoformat() if rec.resolved_at else None,
    }


@router.delete("/recommendations/{rec_id}", status_code=204, tags=["数据大屏"])
def delete_recommendation(
    rec_id: int,
    db: Session = Depends(get_db),
    ctx: SiteContext = Depends(require_site_write),
):
    rec = (
        db.query(Recommendation)
        .filter(Recommendation.id == rec_id, Recommendation.site_id == ctx.site.id)
        .first()
    )
    if not rec:
        raise HTTPException(status_code=404, detail="工单不存在")
    db.delete(rec)
    db.commit()


def _calc_trend(current: int, previous: int) -> float | None:
    if previous == 0:
        return 100.0 if current > 0 else None
    return round((current - previous) / previous * 100, 1)
