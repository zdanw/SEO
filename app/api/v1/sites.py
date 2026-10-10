"""客户站点 CRUD 与成员管理。"""
from sqlalchemy import text

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import SiteContext, get_current_user, get_site_context, require_site_write
from app.core.database import get_db
from app.models.client_site import ClientSite
from app.models.site_member import SiteMember
from app.models.user import User
from app.schemas.site import (
    ClientSiteCreate,
    ClientSiteOut,
    ClientSiteUpdate,
    SiteMemberCreate,
    SiteMemberOut,
)
from app.services.site_service import delete_client_site

router = APIRouter()


def _site_out(site: ClientSite, role: str | None = None) -> ClientSiteOut:
    data = ClientSiteOut.model_validate(site)
    data.role = role  # type: ignore[assignment]
    return data


@router.get("", response_model=list[ClientSiteOut])
def list_sites(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[ClientSiteOut]:
    """列出当前用户可访问的客户站点。"""
    if current_user.is_admin:
        sites = (
            db.query(ClientSite)
            .filter(ClientSite.owner_user_id == current_user.id)
            .order_by(ClientSite.created_at.desc())
            .all()
        )
        return [_site_out(s, "admin") for s in sites]

    rows = (
        db.query(ClientSite, SiteMember.role)
        .join(SiteMember, SiteMember.site_id == ClientSite.id)
        .filter(SiteMember.user_id == current_user.id)
        .order_by(ClientSite.created_at.desc())
        .all()
    )
    return [_site_out(site, role) for site, role in rows]


@router.post("", response_model=ClientSiteOut, status_code=status.HTTP_201_CREATED)
def create_site(
    payload: ClientSiteCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ClientSiteOut:
    site = ClientSite(**payload.model_dump(), owner_user_id=current_user.id)
    db.add(site)
    db.flush()
    db.add(SiteMember(site_id=site.id, user_id=current_user.id, role="admin"))
    db.commit()
    db.refresh(site)
    return _site_out(site, "admin")


@router.get("/{site_id}", response_model=ClientSiteOut)
def get_site(
    site_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ClientSiteOut:
    site = db.query(ClientSite).filter(ClientSite.id == site_id).first()
    if not site:
        raise HTTPException(status_code=404, detail="客户站点不存在")
    member = (
        db.query(SiteMember)
        .filter(SiteMember.site_id == site_id, SiteMember.user_id == current_user.id)
        .first()
    )
    if not member and not (current_user.is_admin and site.owner_user_id == current_user.id):
        raise HTTPException(status_code=403, detail="无权访问该客户站点")
    return _site_out(site, member.role if member else "admin")


@router.patch("/{site_id}", response_model=ClientSiteOut)
def update_site(
    site_id: int,
    payload: ClientSiteUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ClientSiteOut:
    site = db.query(ClientSite).filter(ClientSite.id == site_id).first()
    if not site:
        raise HTTPException(status_code=404, detail="客户站点不存在")
    member = (
        db.query(SiteMember)
        .filter(SiteMember.site_id == site_id, SiteMember.user_id == current_user.id)
        .first()
    )
    if not member or member.role != "admin":
        raise HTTPException(status_code=403, detail="仅站点管理员可修改配置")
    for field, val in payload.model_dump(exclude_unset=True).items():
        setattr(site, field, val)
    db.commit()
    db.refresh(site)
    return _site_out(site, member.role)


@router.delete("/{site_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_site(
    site_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> None:
    if not db.query(ClientSite.id).filter(ClientSite.id == site_id).first():
        raise HTTPException(status_code=404, detail="客户站点不存在")
    role = db.execute(
        text(
            "SELECT role FROM site_members WHERE site_id = :site_id AND user_id = :user_id"
        ),
        {"site_id": site_id, "user_id": current_user.id},
    ).scalar()
    if role != "admin":
        raise HTTPException(status_code=403, detail="仅站点管理员可删除站点")
    delete_client_site(db, site_id)
    db.commit()


@router.get("/{site_id}/members", response_model=list[SiteMemberOut])
def list_members(
    site_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[SiteMember]:
    site = db.query(ClientSite).filter(ClientSite.id == site_id).first()
    if not site:
        raise HTTPException(status_code=404, detail="客户站点不存在")
    member = (
        db.query(SiteMember)
        .filter(SiteMember.site_id == site_id, SiteMember.user_id == current_user.id)
        .first()
    )
    if not member:
        raise HTTPException(status_code=403, detail="无权访问该客户站点")
    return (
        db.query(SiteMember)
        .filter(SiteMember.site_id == site_id)
        .order_by(SiteMember.created_at.asc())
        .all()
    )


@router.post("/{site_id}/members", response_model=SiteMemberOut, status_code=status.HTTP_201_CREATED)
def add_member(
    site_id: int,
    payload: SiteMemberCreate,
    db: Session = Depends(get_db),
    ctx: SiteContext = Depends(require_site_write),
) -> SiteMember:
    if ctx.site.id != site_id:
        raise HTTPException(status_code=400, detail="站点 ID 不匹配")
    if ctx.member.role != "admin":
        raise HTTPException(status_code=403, detail="仅站点管理员可管理成员")
    existing = (
        db.query(SiteMember)
        .filter(SiteMember.site_id == site_id, SiteMember.user_id == payload.user_id)
        .first()
    )
    if existing:
        raise HTTPException(status_code=400, detail="该用户已是站点成员")
    member = SiteMember(site_id=site_id, user_id=payload.user_id, role=payload.role)
    db.add(member)
    db.commit()
    db.refresh(member)
    return member
