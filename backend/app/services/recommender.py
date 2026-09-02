"""P4 策略推荐引擎（规则 V1）。

根据文章状态、SERP 排名、社交互动等数据自动生成优化建议工单，写入 recommendations 表。

触发方式：
  1. 手动触发：POST /dashboard/recommendations/generate
  2. 定时任务：Celery Beat 每日调度 alert_tasks.run_recommender
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from app.models.article import Article
from app.models.keyword import Keyword
from app.models.recommendation import Recommendation
from app.models.serp_rank import SerpRankSnapshot
from app.models.social import SocialAccount, SocialPost


def generate_recommendations(db: Session, site_id: int, user_id: int, days_lookback: int = 7) -> list[Recommendation]:
    """扫描文章 + 排名 + 社交数据，生成规则级推荐工单。

    规则：
      ① 发布 7 天排名 > 50 → "PulseForge 再推一次 + 追加 Reddit 互动"
      ② 社交 0 互动 → "修改配图 / 标题加悬念"
      ③ SEO 得分 < 60 → "完善标题/Meta/H2 结构"
      ④ 最近 7 天无 SERP 快照 → "检查爬虫是否正常抓取"
    """
    now = datetime.utcnow()
    cutoff = now - timedelta(days=days_lookback)
    existing_ids: set[int] = set()

    def _avoid_duplicate(rec: Recommendation) -> bool:
        key = (rec.category, rec.severity, rec.article_id or 0, rec.keyword_id or 0)
        if key in existing_ids:
            return False
        existing_ids.add(key)
        return True

    created: list[Recommendation] = []

    # — 规则 ①：发布 ≥ 7 天且排名 > 50 —
    published_articles = (
        db.query(Article)
        .filter(
            Article.site_id == site_id,
            Article.status == "published",
            Article.published_at <= cutoff,
        )
        .all()
    )
    for article in published_articles:
        if not article.keyword:
            continue
        latest_snap = (
            db.query(SerpRankSnapshot)
            .filter(SerpRankSnapshot.keyword_id == article.keyword_id)
            .order_by(SerpRankSnapshot.time.desc())
            .first()
        )
        if latest_snap and latest_snap.rank and latest_snap.rank > 50:
            rec = Recommendation(
                user_id=user_id,
                site_id=site_id,
                article_id=article.id,
                keyword_id=article.keyword_id,
                category="performance",
                severity="warning",
                title=f"关键词「{article.keyword.keyword}」发布 7 天后排名仍 > {latest_snap.rank}",
                description=(
                    f"该文章发布已超过 7 天，当前 SERP 排名第 {latest_snap.rank} 位，"
                    "建议再次通过社交渠道推广以提升排名。"
                ),
                suggestion="通过 PulseForge 再次推送本文，并在 Reddit 相关社区补充互动；同时检查内链是否充分。",
                status="open",
            )
            if _avoid_duplicate(rec):
                db.add(rec)
                created.append(rec)

    # — 规则 ②：已发帖但 0 互动 —
    posted_posts = (
        db.query(SocialPost)
        .join(SocialAccount, SocialPost.account_id == SocialAccount.id)
        .filter(
            SocialAccount.site_id == site_id,
            SocialPost.status == "posted",
            SocialPost.posted_at >= cutoff,
        )
        .all()
    )
    for post in posted_posts:
        engagement = post.engagement or {}
        total_engagement = sum(v for k, v in engagement.items() if isinstance(v, (int, float)))
        if total_engagement == 0:
            rec = Recommendation(
                user_id=user_id,
                site_id=site_id,
                article_id=post.article_id,
                category="social",
                severity="info",
                title=f"社交帖无互动（平台：{post.account.platform if post.account else 'unknown'}）",
                description="文章已发布到社交平台但暂无点赞/评论/分享/点击，建议优化配图或标题。",
                suggestion="修改配图或标题加悬念；考虑更换发布时间段；尝试不同平台的差异化文案。",
                status="open",
            )
            if _avoid_duplicate(rec):
                db.add(rec)
                created.append(rec)

    # — 规则 ③：SEO 得分 < 60 —
    low_seo_articles = (
        db.query(Article)
        .filter(
            Article.site_id == site_id,
            Article.status == "published",
            Article.seo_score < 60,
        )
        .all()
    )
    for article in low_seo_articles:
        rec = Recommendation(
            user_id=user_id,
            site_id=site_id,
            article_id=article.id,
            keyword_id=article.keyword_id,
            category="content",
            severity="warning",
            title=f"文章 SEO 得分较低（{article.seo_score}/100）",
            description="该文章 SEO 检查得分低于 60，可能影响搜索排名。",
            suggestion="优化标题（50-60 字符含关键词）、Meta Description（150-160 字符）、H2 层级含关键词、补充图片 alt 文本。",
            status="open",
        )
        if _avoid_duplicate(rec):
            db.add(rec)
            created.append(rec)

    # — 规则 ④：有关键词但 7 天内无 SERP 快照 —
    active_keywords = (
        db.query(Keyword)
        .filter(Keyword.site_id == site_id, Keyword.status == "active")
        .all()
    )
    for kw in active_keywords:
        recent_snap = (
            db.query(SerpRankSnapshot)
            .filter(
                SerpRankSnapshot.keyword_id == kw.id,
                SerpRankSnapshot.time >= cutoff,
            )
            .first()
        )
        if not recent_snap:
            rec = Recommendation(
                user_id=user_id,
                site_id=site_id,
                keyword_id=kw.id,
                category="performance",
                severity="critical",
                title=f"关键词「{kw.keyword}」近 7 天无排名数据",
                description="该活跃关键词已超过 7 天未更新 SERP 排名，爬虫可能异常。",
                suggestion="检查代理池健康状态、SERP 爬虫任务是否正常运行；手动触发一次抓取测试。",
                status="open",
            )
            if _avoid_duplicate(rec):
                db.add(rec)
                created.append(rec)

    db.commit()
    return created


def get_open_recommendations(
    db: Session,
    site_id: int,
    category: str | None = None,
    severity: str | None = None,
    status: str | None = None,
    page: int = 1,
    size: int = 50,
) -> tuple[list[Recommendation], int]:
    """分页查询工单，返回 (records, total)。"""
    q = db.query(Recommendation).filter(Recommendation.site_id == site_id)
    if category:
        q = q.filter(Recommendation.category == category)
    if severity:
        q = q.filter(Recommendation.severity == severity)
    if status:
        q = q.filter(Recommendation.status == status)

    total = q.count()
    records = (
        q.order_by(
            Recommendation.severity.asc(),   # critical 优先
            Recommendation.created_at.desc(),
        )
        .offset((page - 1) * size)
        .limit(size)
        .all()
    )
    return records, total


def update_recommendation_status(
    db: Session,
    rec_id: int,
    site_id: int,
    status: str,
) -> Recommendation | None:
    """更新工单状态（open / in_progress / resolved / ignored）。"""
    rec = (
        db.query(Recommendation)
        .filter(Recommendation.id == rec_id, Recommendation.site_id == site_id)
        .first()
    )
    if not rec:
        return None
    rec.status = status
    if status == "resolved":
        rec.resolved_at = datetime.utcnow()
    db.commit()
    db.refresh(rec)
    return rec
