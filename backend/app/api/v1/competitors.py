"""竞品 CRUD + 排名查询 API。"""
from __future__ import annotations

from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.api.deps import SiteContext, get_current_user, get_site_context, require_site_write
from app.core.database import get_db
from app.models.competitor import Competitor
from app.models.serp_rank import CompetitorRankSnapshot
from app.models.user import User
from app.schemas.monitor import CompetitorCreate, CompetitorOut

router = APIRouter()


@router.get("", response_model=list[CompetitorOut])
def list_competitors(
    db: Session = Depends(get_db),
    ctx: SiteContext = Depends(get_site_context),
):
    return (
        db.query(Competitor)
        .filter(Competitor.site_id == ctx.site.id)
        .order_by(Competitor.created_at.desc())
        .all()
    )


@router.post("", response_model=CompetitorOut, status_code=201)
def create_competitor(
    payload: CompetitorCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    ctx: SiteContext = Depends(require_site_write),
):
    existing = (
        db.query(Competitor)
        .filter(Competitor.site_id == ctx.site.id, Competitor.domain == payload.domain)
        .first()
    )
    if existing:
        raise HTTPException(status_code=400, detail="该竞品域名已存在")
    comp = Competitor(
        user_id=current_user.id,
        site_id=ctx.site.id,
        domain=payload.domain,
        name=payload.name,
    )
    db.add(comp)
    db.commit()
    db.refresh(comp)
    return comp


@router.delete("/{competitor_id}", status_code=204)
def delete_competitor(
    competitor_id: int,
    db: Session = Depends(get_db),
    ctx: SiteContext = Depends(require_site_write),
):
    comp = (
        db.query(Competitor)
        .filter(Competitor.id == competitor_id, Competitor.site_id == ctx.site.id)
        .first()
    )
    if not comp:
        raise HTTPException(404, "竞品不存在")
    db.query(CompetitorRankSnapshot).filter(
        CompetitorRankSnapshot.competitor_id == competitor_id
    ).delete()
    db.delete(comp)
    db.commit()


@router.get("/{competitor_id}/ranks")
def get_competitor_ranks(
    competitor_id: int,
    days: int = Query(default=30, ge=1, le=365),
    db: Session = Depends(get_db),
    ctx: SiteContext = Depends(get_site_context),
):
    comp = (
        db.query(Competitor)
        .filter(Competitor.id == competitor_id, Competitor.site_id == ctx.site.id)
        .first()
    )
    if not comp:
        raise HTTPException(404, "竞品不存在")

    start = datetime.utcnow() - timedelta(days=days)
    sql = text(
        """
        SELECT crs.time, crs.keyword_id, crs.domain, crs.rank, crs.target_url,
               k.keyword
        FROM competitor_rank_snapshots crs
        JOIN keywords k ON k.id = crs.keyword_id
        WHERE crs.competitor_id = :cid AND crs.time >= :start AND k.site_id = :site_id
        ORDER BY crs.time ASC
        """
    )
    rows = db.execute(
        sql, {"cid": competitor_id, "start": start, "site_id": ctx.site.id}
    ).mappings().all()
    return [
        {
            "time": r["time"].isoformat(),
            "keyword_id": r["keyword_id"],
            "keyword": r["keyword"],
            "domain": r["domain"],
            "rank": r["rank"],
            "target_url": r["target_url"],
        }
        for r in rows
    ]
