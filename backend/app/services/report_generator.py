"""客户站点月度 SEO 报告生成。"""
from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.article import Article
from app.models.backlink import Backlink
from app.models.client_site import ClientSite
from app.models.keyword import Keyword
from app.models.recommendation import Recommendation
from app.models.serp_rank import SerpRankSnapshot


def generate_monthly_report(db: Session, site: ClientSite, days: int = 30) -> dict:
    """汇总站点 SEO 数据，生成月度报告。"""
    start = datetime.utcnow() - timedelta(days=days)
    site_id = site.id

    kw_ids = [
        row[0]
        for row in db.query(Keyword.id)
        .filter(Keyword.site_id == site_id, Keyword.status == "active")
        .all()
    ]

    top10_count = 0
    if kw_ids:
        top10_count = (
            db.query(func.count())
            .select_from(SerpRankSnapshot)
            .filter(
                SerpRankSnapshot.keyword_id.in_(kw_ids),
                SerpRankSnapshot.time >= start,
                SerpRankSnapshot.rank.isnot(None),
                SerpRankSnapshot.rank <= 10,
            )
            .scalar()
            or 0
        )

    new_articles = (
        db.query(func.count(Article.id))
        .filter(Article.site_id == site_id, Article.published_at >= start)
        .scalar()
        or 0
    )
    bl_new = (
        db.query(func.count(Backlink.id))
        .filter(Backlink.site_id == site_id, Backlink.first_seen_at >= start)
        .scalar()
        or 0
    )
    bl_lost = (
        db.query(func.count(Backlink.id))
        .filter(
            Backlink.site_id == site_id,
            Backlink.lost_at.isnot(None),
            Backlink.lost_at >= start,
        )
        .scalar()
        or 0
    )
    open_recs = (
        db.query(func.count(Recommendation.id))
        .filter(Recommendation.site_id == site_id, Recommendation.status == "open")
        .scalar()
        or 0
    )

    return {
        "site": {
            "id": site.id,
            "name": site.name,
            "domain": site.domain,
        },
        "period_days": days,
        "generated_at": datetime.utcnow().isoformat(),
        "summary": {
            "active_keywords": len(kw_ids),
            "top10_rankings": top10_count,
            "new_articles": new_articles,
            "backlinks_new": bl_new,
            "backlinks_lost": bl_lost,
            "backlinks_net": bl_new - bl_lost,
            "open_recommendations": open_recs,
        },
        "markdown": _to_markdown(site, days, len(kw_ids), top10_count, new_articles, bl_new, bl_lost, open_recs),
    }


def _to_markdown(
    site: ClientSite,
    days: int,
    kw_count: int,
    top10: int,
    articles: int,
    bl_new: int,
    bl_lost: int,
    open_recs: int,
) -> str:
    return f"""# {site.name} SEO 月度报告

**域名**: {site.domain}
**统计周期**: 近 {days} 天
**生成时间**: {datetime.utcnow().strftime('%Y-%m-%d %H:%M')} UTC

## 核心指标

| 指标 | 数值 |
|------|------|
| 监控关键词数 | {kw_count} |
| Top10 排名次数 | {top10} |
| 新发布文章 | {articles} |
| 新增外链 | {bl_new} |
| 丢失外链 | {bl_lost} |
| 外链净增 | {bl_new - bl_lost} |
| 待处理优化建议 | {open_recs} |

## 建议

- 优先处理 {open_recs} 条待办优化工单
- 关注排名波动较大的关键词并调整内容策略
- 定期检查外链存活率，及时挽回丢失链接
"""
