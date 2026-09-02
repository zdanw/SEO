"""SEO 检查器 API：对文章做静态 SEO 分析 + 内链推荐 + 批量审计。"""
import json
import logging
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal

from fastapi import APIRouter, Body, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import SiteContext, get_site_context, require_site_write
from app.core.database import get_db
from app.models.article import Article, InternalLink
from app.models.seo_audit import SeoAudit, SeoAuditPage
from app.schemas.seo_audit import SeoAuditCreate, SeoAuditOut, SeoAuditPageOut, UrlCheckRequest
from app.services.page_fetcher import fetch_page_content
from app.services.seo_analyzer import SeoReport, analyze as seo_analyze
from app.services.seo_audit_service import run_site_audit
from app.services.pagespeed_insights import PageSpeedError, fetch_cwv, is_configured as psi_configured
from app.services.seoscan_runner import SeoscanError, audit_url, seoscan_to_check_items
from app.services.content_optimizer import score_content_for_serp

router = APIRouter()
logger = logging.getLogger(__name__)


class SeoCheckRequest(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    content: str = ""
    meta_description: str | None = None
    keyword: str | None = None
    cover_image_url: str | None = None


class SeoCheckResponse(BaseModel):
    total_score: float
    items: list[dict]
    ai_detected_score: float
    ai_detail: dict = {}
    serp_detail: dict = {}
    technical_audit: dict = {}
    cwv_estimate: dict
    internal_links_recommended: list[dict] = []


class SerpScoreRequest(BaseModel):
    content: str = ""
    keyword: str = Field(min_length=1, max_length=200)
    target_word_count: int | None = None
    region: str | None = None


@router.post("/check", response_model=SeoCheckResponse, summary="对文章内容做 SEO 静态检查（不入库）")
def check_seo(payload: SeoCheckRequest) -> SeoCheckResponse:
    report: SeoReport = seo_analyze(
        title=payload.title,
        content=payload.content,
        meta_description=payload.meta_description,
        keyword=payload.keyword,
        cover_image_url=payload.cover_image_url,
    )
    return SeoCheckResponse(**report.to_dict())


@router.post("/check-url", response_model=SeoCheckResponse, summary="抓取外部 URL 并用 seoscan 做技术 SEO 审计")
def check_url(
    payload: UrlCheckRequest,
    ctx: SiteContext = Depends(get_site_context),
) -> SeoCheckResponse:
    seoscan_report: dict
    psi_cwv: dict | None = None

    with ThreadPoolExecutor(max_workers=2) as pool:
        seoscan_future = pool.submit(audit_url, payload.url)
        psi_future = pool.submit(fetch_cwv, payload.url) if psi_configured() else None

        try:
            seoscan_report = seoscan_future.result()
        except SeoscanError as exc:
            raise HTTPException(status_code=400, detail=f"seoscan 审计失败: {exc}") from exc

        if psi_future:
            try:
                psi_cwv = psi_future.result()
            except PageSpeedError as exc:
                logger.warning("PageSpeed Insights 失败 %s: %s", payload.url, exc)

    ai_score = 0.0
    ai_detail: dict = {}
    serp_detail: dict = {}
    cwv_estimate: dict = psi_cwv or {
        "source": "none",
        "note": "未配置 PAGESPEED_API_KEY，CWV 需配置后通过 PageSpeed Insights 实测",
    }

    # 有关键词时补充正文向分析与 SERP 评分
    if payload.keyword:
        try:
            page = fetch_page_content(payload.url)
            content_report = seo_analyze(
                title=page["title"],
                content=page["content"],
                meta_description=page["meta_description"],
                keyword=payload.keyword,
                cover_image_url=page.get("cover_image_url") or None,
            )
            ai_score = float(content_report.ai_detected_score)
            ai_detail = content_report.ai_detail
            serp_detail = content_report.serp_detail
            # PSI 实测优先于静态预估
            if not psi_cwv:
                cwv_estimate = content_report.cwv_estimate
        except Exception:
            pass

    items = seoscan_to_check_items(seoscan_report)
    return SeoCheckResponse(
        total_score=float(seoscan_report["score"]),
        items=items,
        ai_detected_score=ai_score,
        ai_detail=ai_detail,
        serp_detail=serp_detail,
        technical_audit=seoscan_report,
        cwv_estimate=cwv_estimate,
    )


@router.post("/serp-score", summary="SERP 内容优化评分（Content Optimizer）")
def serp_score(payload: SerpScoreRequest) -> dict:
    try:
        result = score_content_for_serp(
            payload.content,
            payload.keyword,
            target_word_count=payload.target_word_count,
            region=payload.region,
        )
        return result.to_dict()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/check/{article_id}", response_model=SeoCheckResponse)
def check_and_save(
    article_id: int,
    keyword: str | None = Body(default=None, embed=True),
    db: Session = Depends(get_db),
    ctx: SiteContext = Depends(get_site_context),
) -> SeoCheckResponse:
    article = (
        db.query(Article)
        .filter(Article.id == article_id, Article.site_id == ctx.site.id)
        .first()
    )
    if not article:
        raise HTTPException(status_code=404, detail="文章不存在")
    report = seo_analyze(
        title=article.title,
        content=article.content or "",
        meta_description=article.meta_description,
        keyword=keyword,
        cover_image_url=article.cover_image_url,
    )
    article.seo_score = report.total_score
    article.ai_detected_score = report.ai_detected_score
    article.seo_detail = json.dumps(report.to_dict(), ensure_ascii=False)
    db.commit()
    db.refresh(article)
    return SeoCheckResponse(**report.to_dict())


@router.get("/internal-links/{article_id}", response_model=list[dict])
def recommend_internal_links(
    article_id: int,
    limit: int = 5,
    db: Session = Depends(get_db),
    ctx: SiteContext = Depends(get_site_context),
) -> list[dict]:
    article = (
        db.query(Article)
        .filter(Article.id == article_id, Article.site_id == ctx.site.id)
        .first()
    )
    if not article:
        raise HTTPException(status_code=404, detail="文章不存在")

    candidates = (
        db.query(Article)
        .filter(
            Article.site_id == ctx.site.id,
            Article.id != article_id,
            Article.content.isnot(None),
        )
        .limit(200)
        .all()
    )
    if not candidates:
        return []

    target_words = set(_tokenize(article.title + " " + (article.content or "")[:500]))
    scored: list[tuple[float, Article, str]] = []
    for cand in candidates:
        cand_words = set(_tokenize(cand.title + " " + (cand.content or "")[:500]))
        if not cand_words:
            continue
        overlap = len(target_words & cand_words)
        union = len(target_words | cand_words)
        jaccard = overlap / union if union else 0
        if jaccard > 0:
            anchor = cand.title[:50]
            scored.append((jaccard, cand, anchor))

    scored.sort(key=lambda x: x[0], reverse=True)
    result: list[dict] = []
    for score, cand, anchor in scored[:limit]:
        existing = (
            db.query(InternalLink)
            .filter(
                InternalLink.source_article_id == article_id,
                InternalLink.target_article_id == cand.id,
            )
            .first()
        )
        if not existing:
            link = InternalLink(
                source_article_id=article_id,
                target_article_id=cand.id,
                anchor_text=anchor,
                relevance_score=Decimal(str(round(score, 3))),
            )
            db.add(link)
        result.append({
            "target_article_id": cand.id,
            "target_title": cand.title,
            "anchor_text": anchor,
            "relevance_score": round(score, 3),
        })
    db.commit()
    return result


@router.post("/audits", response_model=SeoAuditOut, status_code=status.HTTP_201_CREATED)
def create_audit(
    payload: SeoAuditCreate,
    db: Session = Depends(get_db),
    ctx: SiteContext = Depends(require_site_write),
) -> SeoAuditOut:
    audit = run_site_audit(db, ctx.site, ctx.member.user_id, max_pages=payload.max_pages)
    pages = (
        db.query(SeoAuditPage)
        .filter(SeoAuditPage.audit_id == audit.id)
        .order_by(SeoAuditPage.score.asc().nullsfirst())
        .all()
    )
    return SeoAuditOut(
        id=audit.id,
        site_id=audit.site_id,
        status=audit.status,
        total_pages=audit.total_pages,
        scanned_pages=audit.scanned_pages,
        avg_score=audit.avg_score,
        summary=audit.summary,
        created_at=audit.created_at,
        completed_at=audit.completed_at,
        pages=[SeoAuditPageOut.model_validate(p) for p in pages],
    )


@router.get("/audits", response_model=list[SeoAuditOut])
def list_audits(
    db: Session = Depends(get_db),
    ctx: SiteContext = Depends(get_site_context),
) -> list[SeoAuditOut]:
    audits = (
        db.query(SeoAudit)
        .filter(SeoAudit.site_id == ctx.site.id)
        .order_by(SeoAudit.created_at.desc())
        .limit(20)
        .all()
    )
    result = []
    for audit in audits:
        pages = (
            db.query(SeoAuditPage)
            .filter(SeoAuditPage.audit_id == audit.id)
            .order_by(SeoAuditPage.score.asc().nullsfirst())
            .limit(10)
            .all()
        )
        result.append(
            SeoAuditOut(
                id=audit.id,
                site_id=audit.site_id,
                status=audit.status,
                total_pages=audit.total_pages,
                scanned_pages=audit.scanned_pages,
                avg_score=audit.avg_score,
                summary=audit.summary,
                created_at=audit.created_at,
                completed_at=audit.completed_at,
                pages=[SeoAuditPageOut.model_validate(p) for p in pages],
            )
        )
    return result


@router.get("/audits/{audit_id}", response_model=SeoAuditOut)
def get_audit(
    audit_id: int,
    db: Session = Depends(get_db),
    ctx: SiteContext = Depends(get_site_context),
) -> SeoAuditOut:
    audit = (
        db.query(SeoAudit)
        .filter(SeoAudit.id == audit_id, SeoAudit.site_id == ctx.site.id)
        .first()
    )
    if not audit:
        raise HTTPException(status_code=404, detail="审计记录不存在")
    pages = (
        db.query(SeoAuditPage)
        .filter(SeoAuditPage.audit_id == audit.id)
        .order_by(SeoAuditPage.score.asc().nullsfirst())
        .all()
    )
    return SeoAuditOut(
        id=audit.id,
        site_id=audit.site_id,
        status=audit.status,
        total_pages=audit.total_pages,
        scanned_pages=audit.scanned_pages,
        avg_score=audit.avg_score,
        summary=audit.summary,
        created_at=audit.created_at,
        completed_at=audit.completed_at,
        pages=[SeoAuditPageOut.model_validate(p) for p in pages],
    )


def _tokenize(text: str) -> list[str]:
    import re as _re
    tokens = _re.findall(r"[a-zA-Z]{2,}", text.lower())
    chinese = _re.sub(r"[^\u4e00-\u9fa5]", "", text)
    for i in range(len(chinese) - 1):
        tokens.append(chinese[i : i + 2])
    return tokens
