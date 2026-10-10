"""关键词库 CRUD API。"""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import SiteContext, get_current_user, get_site_context, require_site_write
from app.core.database import get_db
from app.models.user import User
from app.models.keyword import Keyword
from app.models.serp_rank import SerpRankSnapshot
from app.schemas.keyword import KeywordCreate, KeywordOut

router = APIRouter()


@router.get("", response_model=list[KeywordOut])
def list_keywords(
    db: Session = Depends(get_db),
    ctx: SiteContext = Depends(get_site_context),
) -> list[Keyword]:
    return (
        db.query(Keyword)
        .filter(Keyword.site_id == ctx.site.id)
        .order_by(Keyword.priority.asc(), Keyword.created_at.desc())
        .all()
    )


@router.post("", response_model=KeywordOut, status_code=status.HTTP_201_CREATED)
def create_keyword(
    payload: KeywordCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    ctx: SiteContext = Depends(require_site_write),
) -> Keyword:
    kw = Keyword(**payload.model_dump(), user_id=current_user.id, site_id=ctx.site.id)
    db.add(kw)
    db.commit()
    db.refresh(kw)
    return kw


@router.delete("/{keyword_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_keyword(
    keyword_id: int,
    db: Session = Depends(get_db),
    ctx: SiteContext = Depends(require_site_write),
) -> None:
    kw = (
        db.query(Keyword)
        .filter(Keyword.id == keyword_id, Keyword.site_id == ctx.site.id)
        .first()
    )
    if not kw:
        raise HTTPException(status_code=404, detail="关键词不存在")
    db.query(SerpRankSnapshot).filter(SerpRankSnapshot.keyword_id == keyword_id).delete()
    db.delete(kw)
    db.commit()
