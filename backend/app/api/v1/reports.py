"""月度报告导出 API。"""
from fastapi import APIRouter, Depends, Query
from fastapi.responses import PlainTextResponse
from sqlalchemy.orm import Session

from app.api.deps import SiteContext, get_site_context
from app.core.database import get_db
from app.services.report_generator import generate_monthly_report

router = APIRouter()


@router.get("/monthly")
def monthly_report(
    days: int = Query(default=30, ge=7, le=90),
    db: Session = Depends(get_db),
    ctx: SiteContext = Depends(get_site_context),
):
    """生成客户站点月度 SEO 报告（JSON）。"""
    return generate_monthly_report(db, ctx.site, days=days)


@router.get("/monthly/export", response_class=PlainTextResponse)
def export_monthly_report(
    days: int = Query(default=30, ge=7, le=90),
    db: Session = Depends(get_db),
    ctx: SiteContext = Depends(get_site_context),
) -> PlainTextResponse:
    """导出 Markdown 格式月度报告。"""
    data = generate_monthly_report(db, ctx.site, days=days)
    filename = f"seo-report-{ctx.site.domain}-{days}d.md"
    return PlainTextResponse(
        content=data["markdown"],
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        media_type="text/markdown; charset=utf-8",
    )
