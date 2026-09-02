"""外链 CRUD + 存活检测 API。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.deps import SiteContext, get_current_user, get_site_context, require_site_write
from app.core.database import get_db
from app.models.backlink import Backlink
from app.models.user import User
from app.schemas.monitor import BacklinkCreate, BacklinkOut, BacklinkUpdate
from app.services.backlink_monitor import get_backlink_monitor
router = APIRouter()


@router.get("", response_model=list[BacklinkOut])
def list_backlinks(
    is_alive: bool | None = None,
    page: int = Query(default=1, ge=1),
    size: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
    ctx: SiteContext = Depends(get_site_context),
):
    q = db.query(Backlink).filter(Backlink.site_id == ctx.site.id)
    if is_alive is not None:
        q = q.filter(Backlink.is_alive == is_alive)
    return q.order_by(Backlink.first_seen_at.desc()).offset((page - 1) * size).limit(size).all()


@router.post("", response_model=BacklinkOut, status_code=201)
def create_backlink(
    payload: BacklinkCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    ctx: SiteContext = Depends(require_site_write),
):
    existing = (
        db.query(Backlink)
        .filter(
            Backlink.target_url == payload.target_url,
            Backlink.source_url == payload.source_url,
        )
        .first()
    )
    if existing:
        raise HTTPException(status_code=400, detail="该外链记录已存在")
    link = Backlink(
        user_id=current_user.id,
        site_id=ctx.site.id,
        target_url=payload.target_url,
        source_url=payload.source_url,
        anchor_text=payload.anchor_text,
        domain_authority=payload.domain_authority,
        is_alive=True,
    )
    db.add(link)
    db.commit()
    db.refresh(link)
    return link


@router.patch("/{backlink_id}", response_model=BacklinkOut)
def update_backlink(
    backlink_id: int,
    payload: BacklinkUpdate,
    db: Session = Depends(get_db),
    ctx: SiteContext = Depends(require_site_write),
):
    link = (
        db.query(Backlink)
        .filter(Backlink.id == backlink_id, Backlink.site_id == ctx.site.id)
        .first()
    )
    if not link:
        raise HTTPException(404, "外链不存在")
    for field, val in payload.model_dump(exclude_unset=True).items():
        setattr(link, field, val)
    db.commit()
    db.refresh(link)
    return link


@router.delete("/{backlink_id}", status_code=204)
def delete_backlink(
    backlink_id: int,
    db: Session = Depends(get_db),
    ctx: SiteContext = Depends(require_site_write),
):
    link = (
        db.query(Backlink)
        .filter(Backlink.id == backlink_id, Backlink.site_id == ctx.site.id)
        .first()
    )
    if not link:
        raise HTTPException(404, "外链不存在")
    db.delete(link)
    db.commit()


@router.post("/{backlink_id}/check", response_model=BacklinkOut)
def check_one(
    backlink_id: int,
    db: Session = Depends(get_db),
    ctx: SiteContext = Depends(require_site_write),
):
    link = (
        db.query(Backlink)
        .filter(Backlink.id == backlink_id, Backlink.site_id == ctx.site.id)
        .first()
    )
    if not link:
        raise HTTPException(404, "外链不存在")
    monitor = get_backlink_monitor()
    monitor.check_one(db, link)
    db.refresh(link)
    return link


@router.post("/check-all")
def check_all(
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_db),
    ctx: SiteContext = Depends(require_site_write),
):
    monitor = get_backlink_monitor()
    return monitor.check_batch(db, ctx.site.id, limit=limit)
