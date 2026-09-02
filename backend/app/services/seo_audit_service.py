"""站点 SEO 批量审计服务（seoscan 技术审计）。"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from decimal import Decimal

from sqlalchemy.orm import Session

from app.models.client_site import ClientSite
from app.models.seo_audit import SeoAudit, SeoAuditPage
from app.services.page_fetcher import discover_sitemap_urls, url_belongs_to_domain
from app.services.seoscan_runner import SeoscanError, audit_url

logger = logging.getLogger(__name__)


def run_site_audit(
    db: Session,
    site: ClientSite,
    user_id: int,
    *,
    max_pages: int = 50,
) -> SeoAudit:
    """对客户站点执行批量 SEO 审计（seoscan）。"""
    audit = SeoAudit(
        site_id=site.id,
        user_id=user_id,
        status="running",
        total_pages=0,
        scanned_pages=0,
    )
    db.add(audit)
    db.commit()
    db.refresh(audit)

    urls: list[str] = []
    if site.sitemap_url:
        try:
            urls = discover_sitemap_urls(site.sitemap_url, max_urls=max_pages)
        except Exception:
            urls = []
    if not urls:
        domain = site.domain.removeprefix("www.")
        urls = [f"https://{domain}/"]

    urls = [u for u in urls if url_belongs_to_domain(u, site.domain)][:max_pages]
    audit.total_pages = len(urls)
    db.commit()

    scores: list[float] = []
    for url in urls:
        try:
            report = audit_url(url)
            score = float(report["score"])
            issues = len(report.get("top_issues") or [])
            scores.append(score)
            db.add(
                SeoAuditPage(
                    audit_id=audit.id,
                    url=url,
                    title=(report.get("title") or "")[:300] or None,
                    score=Decimal(str(round(score, 2))),
                    issues_count=issues,
                    detail_json=json.dumps(report, ensure_ascii=False),
                )
            )
        except SeoscanError as exc:
            logger.warning("seoscan 审计失败 %s: %s", url, exc)
            db.add(
                SeoAuditPage(
                    audit_id=audit.id,
                    url=url,
                    title=None,
                    score=None,
                    issues_count=1,
                    detail_json=json.dumps({"error": str(exc), "engine": "seoscan"}, ensure_ascii=False),
                )
            )
        except Exception as exc:
            logger.exception("审计异常 %s", url)
            db.add(
                SeoAuditPage(
                    audit_id=audit.id,
                    url=url,
                    title=None,
                    score=None,
                    issues_count=1,
                    detail_json=json.dumps({"error": str(exc)}, ensure_ascii=False),
                )
            )
        audit.scanned_pages += 1
        db.commit()

    audit.status = "completed"
    audit.completed_at = datetime.utcnow()
    if scores:
        audit.avg_score = Decimal(str(round(sum(scores) / len(scores), 2)))
        low_pages = sum(1 for s in scores if s < 60)
        audit.summary = (
            f"seoscan 共扫描 {audit.scanned_pages} 页，平均分 {audit.avg_score}。"
            f"{'有 ' + str(low_pages) + ' 页低于 60 分，建议优先优化。' if low_pages else '整体技术 SEO 健康度良好。'}"
        )
    else:
        audit.summary = "未能成功扫描任何页面，请检查 sitemap、站点可访问性或 Node.js/npx 环境。"
    db.commit()
    db.refresh(audit)
    return audit
