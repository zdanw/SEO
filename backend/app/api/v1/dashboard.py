"""数据大屏 API：汇总卡片、排名趋势。"""
from __future__ import annotations

from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, text
from sqlalchemy.orm import Session

from app.api.deps import SiteContext, get_site_context
from app.core.database import get_db
from app.models.keyword import Keyword
from app.models.reddit import RedditComment, RedditPost, RedditPostMetric
from app.models.serp_rank import SerpRankSnapshot

router = APIRouter()


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
                SerpRankSnapshot.crawl_status == "success",
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
                SerpRankSnapshot.crawl_status == "success",
                SerpRankSnapshot.rank.isnot(None),
            )
            .scalar() or 0
        )
        top10_ratio = round(latest_top10 / total_latest * 100, 1) if total_latest else 0
    else:
        top10_ratio = 0

    posted_posts = (
        db.query(func.count(RedditPost.id))
        .filter(
            RedditPost.site_id == site_id,
            RedditPost.status == "posted",
            RedditPost.published_at >= start,
        )
        .scalar() or 0
    )
    prev_posted = (
        db.query(func.count(RedditPost.id))
        .filter(
            RedditPost.site_id == site_id,
            RedditPost.status == "posted",
            RedditPost.published_at >= prev_start,
            RedditPost.published_at < prev_end,
        )
        .scalar() or 0
    )

    posted_comments = (
        db.query(func.count(RedditComment.id))
        .filter(
            RedditComment.site_id == site_id,
            RedditComment.status == "posted",
            RedditComment.published_at >= start,
        )
        .scalar() or 0
    )
    prev_comments = (
        db.query(func.count(RedditComment.id))
        .filter(
            RedditComment.site_id == site_id,
            RedditComment.status == "posted",
            RedditComment.published_at >= prev_start,
            RedditComment.published_at < prev_end,
        )
        .scalar() or 0
    )

    engagement_score = (
        db.query(func.coalesce(func.sum(RedditPostMetric.score), 0))
        .join(RedditPost, RedditPostMetric.post_id == RedditPost.id)
        .filter(
            RedditPost.site_id == site_id,
            RedditPost.status == "posted",
            RedditPost.published_at >= start,
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
        "sample": {
            "rank_scope": "仅统计 crawl_status=success 且 rank 非空的快照",
            "top10_definition": "成功快照中 rank≤10 的占比",
            "social_scope": "Reddit 已发布帖/评与帖子 score 汇总（非旧 SocialPost 表）",
            "time_range": f"近 {days} 天",
            "disclaimer": "排名变化与社交发帖/互动不做自动因果归因，仅供并列观察。",
        },
        "cards": [
            {"label": "监控关键词数", "value": active_keyword_count, "trend": None, "color": "#409EFF"},
            {"label": "关键词 Top10 占比", "value": f"{top10_ratio}%", "trend": None, "color": "#67C23A"},
            {"label": "Reddit 成功发帖", "value": posted_posts, "trend": _calc_trend(posted_posts, prev_posted), "color": "#E6A23C"},
            {
                "label": "Reddit 评论发布",
                "value": posted_comments,
                "trend": _calc_trend(posted_comments, prev_comments),
                "color": "#F56C6C",
            },
        ],
        "extra": {"reddit_post_score_sum": int(engagement_score)},
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
