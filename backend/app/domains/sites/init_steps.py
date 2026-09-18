"""基础初始化步骤实现（GSC 不在此链中）。"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup
from sqlalchemy.orm import Session

from app.models.client_site import ClientSite
from app.models.competitor import Competitor
from app.models.content_asset import ContentAsset
from app.models.reddit import RedditCommunity
from app.models.technical_audit import TechnicalAudit
from app.models.topic import Topic

logger = logging.getLogger(__name__)

STEP_LABELS: dict[str, str] = {
    "crawl": "站点爬取",
    "technical": "技术 SEO 审计",
    "content_inventory": "内容资产盘点",
    "topics": "主题 / 查询种子",
    "serp_sample": "SERP 采样",
    "competitors": "竞品基线",
    "ai_seed": "AI Search 种子",
    "reddit_seed": "Reddit 情报种子",
    "build_opportunities": "生成增长机会",
}

INIT_STEPS: tuple[str, ...] = tuple(STEP_LABELS.keys())

_CRAWL_LIMIT = 20
_AI_SEED_LIMIT = 5


class StepSkipped(Exception):
    """步骤因缺凭证/无数据/无 Provider 而跳过（不阻断 pipeline）。"""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


def _site_or_raise(db: Session, site_id: int) -> ClientSite:
    site = db.get(ClientSite, site_id)
    if not site:
        raise ValueError(f"site {site_id} not found")
    return site


def _base_url(site: ClientSite) -> str:
    domain = (site.domain or "").strip()
    if not domain:
        raise StepSkipped("站点未设置 domain")
    if domain.startswith("http://") or domain.startswith("https://"):
        return domain.rstrip("/")
    return f"https://{domain}".rstrip("/")


def _fetch_html(url: str, timeout: float = 15.0) -> tuple[str, str]:
    headers = {"User-Agent": "SEO-GrowthOS-Init/1.0"}
    with httpx.Client(follow_redirects=True, timeout=timeout, headers=headers) as client:
        resp = client.get(url)
        resp.raise_for_status()
        return str(resp.url), resp.text


def _parse_title(html: str) -> str | None:
    soup = BeautifulSoup(html, "lxml")
    if soup.title and soup.title.string:
        return soup.title.string.strip()[:500]
    h1 = soup.find("h1")
    if h1 and h1.get_text(strip=True):
        return h1.get_text(strip=True)[:500]
    return None


def _extract_links(base: str, html: str) -> list[str]:
    soup = BeautifulSoup(html, "lxml")
    host = urlparse(base).netloc
    out: list[str] = []
    seen: set[str] = set()
    for a in soup.find_all("a", href=True):
        href = str(a["href"]).strip()
        if href.startswith("#") or href.startswith("mailto:") or href.startswith("javascript:"):
            continue
        full = urljoin(base, href)
        parsed = urlparse(full)
        if parsed.netloc != host:
            continue
        clean = f"{parsed.scheme}://{parsed.netloc}{parsed.path}".rstrip("/") or full
        if clean in seen:
            continue
        seen.add(clean)
        out.append(clean)
        if len(out) >= _CRAWL_LIMIT:
            break
    return out


def _parse_sitemap_urls(xml_text: str, limit: int = _CRAWL_LIMIT) -> list[str]:
    urls = re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", xml_text, flags=re.I)
    dedup: list[str] = []
    seen: set[str] = set()
    for u in urls:
        if u in seen:
            continue
        seen.add(u)
        dedup.append(u.rstrip("/"))
        if len(dedup) >= limit:
            break
    return dedup


def run_crawl(db: Session, site_id: int, context: dict[str, Any] | None = None) -> dict[str, Any]:
    site = _site_or_raise(db, site_id)
    base = _base_url(site)
    pages: list[dict[str, str]] = []
    try:
        final_url, html = _fetch_html(base)
        title = _parse_title(html) or site.name or site.domain
        pages.append({"url": final_url, "title": title})
        for link in _extract_links(final_url, html):
            if any(p["url"] == link for p in pages):
                continue
            pages.append({"url": link, "title": link.rsplit("/", 1)[-1] or link})
            if len(pages) >= _CRAWL_LIMIT:
                break
    except Exception as exc:  # noqa: BLE001
        logger.warning("crawl homepage failed site=%s: %s", site_id, exc)
        raise StepSkipped(f"首页不可达: {exc}") from exc

    if site.sitemap_url:
        try:
            _, xml = _fetch_html(site.sitemap_url, timeout=20.0)
            for u in _parse_sitemap_urls(xml):
                if any(p["url"] == u for p in pages):
                    continue
                pages.append({"url": u, "title": u.rsplit("/", 1)[-1] or u})
                if len(pages) >= _CRAWL_LIMIT:
                    break
        except Exception as exc:  # noqa: BLE001
            logger.info("sitemap skipped site=%s: %s", site_id, exc)

    if context is not None:
        context["crawl_pages"] = pages
    return {"pages": len(pages), "sample": pages[:5], "pages_data": pages}


def _pages_from_context(context: dict[str, Any] | None, site: ClientSite) -> list[dict[str, str]]:
    if context and isinstance(context.get("crawl_pages"), list) and context["crawl_pages"]:
        return [p for p in context["crawl_pages"] if isinstance(p, dict) and p.get("url")]
    try:
        base = _base_url(site)
        return [{"url": base, "title": site.name or site.domain}]
    except StepSkipped:
        return []


def run_technical(db: Session, site_id: int, context: dict[str, Any] | None = None) -> dict[str, Any]:
    site = _site_or_raise(db, site_id)
    pages = _pages_from_context(context, site)
    if not pages:
        raise StepSkipped("无 URL 可审计")

    url = pages[0]["url"]
    findings: list[dict[str, Any]] = []
    score = 70.0
    try:
        _, html = _fetch_html(url)
        soup = BeautifulSoup(html, "lxml")
        if not (soup.title and soup.title.string and soup.title.string.strip()):
            findings.append({"code": "missing_title", "severity": "high", "message": "缺少 <title>"})
            score -= 15
        if not soup.find("h1"):
            findings.append({"code": "missing_h1", "severity": "medium", "message": "缺少 H1"})
            score -= 10
        meta_desc = soup.find("meta", attrs={"name": re.compile("^description$", re.I)})
        if not meta_desc or not meta_desc.get("content"):
            findings.append(
                {"code": "missing_meta_description", "severity": "medium", "message": "缺少 meta description"}
            )
            score -= 10
        if not soup.find("link", rel=lambda v: v and "canonical" in str(v).lower()):
            findings.append({"code": "missing_canonical", "severity": "low", "message": "未检测到 canonical"})
            score -= 5
    except Exception as exc:  # noqa: BLE001
        findings.append({"code": "fetch_failed", "severity": "high", "message": str(exc)[:200]})
        score = 40.0

    row = TechnicalAudit(
        site_id=site_id,
        url=url,
        score=max(0.0, score),
        status="done",
        findings_json=json.dumps(findings, ensure_ascii=False),
        cwv_json=json.dumps({}, ensure_ascii=False),
    )
    db.add(row)
    db.commit()
    return {"audit_id": row.id, "score": row.score, "findings": len(findings)}


def run_content_inventory(db: Session, site_id: int, context: dict[str, Any] | None = None) -> dict[str, Any]:
    from app.domains.content.asset_service import ContentAssetService
    from app.schemas.v2.content_asset import ContentAssetCreate

    site = _site_or_raise(db, site_id)
    pages = _pages_from_context(context, site)
    if not pages:
        raise StepSkipped("无爬取页面可用于内容盘点")

    created = 0
    for page in pages:
        url = page["url"]
        exists = (
            db.query(ContentAsset)
            .filter(ContentAsset.site_id == site_id, ContentAsset.canonical_url == url)
            .first()
        )
        if exists:
            continue
        title = (page.get("title") or url)[:500]
        slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:180] or f"page-{created + 1}"
        try:
            ContentAssetService.create(
                db,
                site_id,
                ContentAssetCreate(
                    asset_type="article",
                    title=title,
                    slug=f"{slug}-{created + 1}",
                    canonical_url=url,
                    summary="Onboarding crawl inventory",
                ),
            )
            created += 1
        except Exception as exc:  # noqa: BLE001
            logger.info("skip asset %s: %s", url, exc)
    existing_count = db.query(ContentAsset).filter(ContentAsset.site_id == site_id).count()
    if created == 0 and existing_count == 0:
        raise StepSkipped("未能创建任何内容资产")
    return {"created": created, "total_assets": existing_count}


def run_topics(db: Session, site_id: int, context: dict[str, Any] | None = None) -> dict[str, Any]:
    from app.domains.search.query_service import SearchQueryService
    from app.domains.search.topic_service import TopicService
    from app.schemas.v2.search_query import SearchQueryCreate
    from app.schemas.v2.topic import TopicCreate

    _ = context
    site = _site_or_raise(db, site_id)
    root_name = (site.industry or site.name or site.domain or "General").strip()[:200]
    existing = (
        db.query(Topic)
        .filter(Topic.site_id == site_id, Topic.parent_id.is_(None))
        .order_by(Topic.id.asc())
        .first()
    )
    if existing:
        root = existing
    else:
        root = TopicService.create(
            db,
            site_id,
            TopicCreate(name=root_name, description="Onboarding root topic", priority=1),
        )

    seed_queries = [root_name, f"{root_name} guide", f"best {root_name}"]
    if site.primary_goal == "more_leads":
        seed_queries.append(f"{root_name} pricing")
    created_q = 0
    for text in seed_queries:
        q = SearchQueryService.create_or_get(
            db,
            site_id,
            SearchQueryCreate(query=text, topic_id=root.id, priority=2),
        )
        if q.topic_id != root.id:
            SearchQueryService.assign_topic(db, q.id, root.id)
        created_q += 1
    return {"topic_id": root.id, "queries_seeded": created_q}


def run_serp_sample(db: Session, site_id: int, context: dict[str, Any] | None = None) -> dict[str, Any]:
    from app.models.search_query import SearchQuery
    from app.models.serp_rank import SerpRankSnapshot

    _ = context
    queries = (
        db.query(SearchQuery)
        .filter(SearchQuery.site_id == site_id)
        .order_by(SearchQuery.priority.asc(), SearchQuery.id.asc())
        .limit(5)
        .all()
    )
    if not queries:
        raise StepSkipped("无 SearchQuery 可采样")

    now = datetime.now(timezone.utc)
    written = 0
    for q in queries:
        db.add(
            SerpRankSnapshot(
                time=now,
                search_query_id=q.id,
                target_url=None,
                rank=None,
                page=None,
                serp_features={"init": True},
                crawl_status="skipped",
                error_message="SERP provider not invoked during baseline init",
            )
        )
        written += 1
    db.commit()
    raise StepSkipped(f"已准备 {len(queries)} 条查询；SERP Provider 未调用（{written} 条 skipped 快照）")


def run_competitors(db: Session, site_id: int, context: dict[str, Any] | None = None) -> dict[str, Any]:
    _ = context
    count = db.query(Competitor).filter(Competitor.site_id == site_id).count()
    if count == 0:
        raise StepSkipped("未配置竞品，跳过竞品基线")
    return {"competitors": count}


def run_ai_seed(db: Session, site_id: int, context: dict[str, Any] | None = None) -> dict[str, Any]:
    from app.domains.ai_search.run_service import AiSearchQueryService, AiSearchRunService
    from app.models.search_query import SearchQuery
    from app.schemas.v2.ai_search import AiSearchQueryCreate

    _ = context
    queries = (
        db.query(SearchQuery)
        .filter(SearchQuery.site_id == site_id)
        .order_by(SearchQuery.priority.asc(), SearchQuery.id.asc())
        .limit(_AI_SEED_LIMIT)
        .all()
    )
    if not queries:
        raise StepSkipped("无查询可用于 AI Search 种子")

    registered = 0
    executed = 0
    for q in queries:
        ai_q = AiSearchQueryService.register(
            db,
            site_id,
            AiSearchQueryCreate(
                query_text=q.query,
                topic_id=q.topic_id,
                country_code=q.country_code or "US",
                language_code=q.language_code or "en",
                device=q.device or "desktop",
                priority=q.priority or 3,
            ),
        )
        registered += 1
        try:
            AiSearchRunService.execute(db, ai_q.id, provider_name="mock")
            executed += 1
        except Exception as exc:  # noqa: BLE001
            logger.info("ai seed run skipped query=%s: %s", ai_q.id, exc)
    if executed == 0:
        raise StepSkipped(f"已注册 {registered} 条 AI 查询，但执行全部失败/跳过")
    return {"registered": registered, "executed": executed}


def run_reddit_seed(db: Session, site_id: int, context: dict[str, Any] | None = None) -> dict[str, Any]:
    _ = context
    communities = db.query(RedditCommunity).filter(RedditCommunity.site_id == site_id).count()
    if communities == 0:
        raise StepSkipped("未配置 Reddit 社区，跳过情报种子")
    return {"communities": communities}


def run_build_opportunities(db: Session, site_id: int, context: dict[str, Any] | None = None) -> dict[str, Any]:
    from app.domains.optimization.engine import RecommendationEngine

    _ = context
    return RecommendationEngine.build(db, site_id)


STEP_RUNNERS = {
    "crawl": run_crawl,
    "technical": run_technical,
    "content_inventory": run_content_inventory,
    "topics": run_topics,
    "serp_sample": run_serp_sample,
    "competitors": run_competitors,
    "ai_seed": run_ai_seed,
    "reddit_seed": run_reddit_seed,
    "build_opportunities": run_build_opportunities,
}
