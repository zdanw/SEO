"""GSC OAuth / 连接状态 API（对齐前端 /api/gsc/*）。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_site_context, require_site_write, SiteContext
from app.api.helpers import api_route
from app.core.database import get_db
from app.core.errors import ValidationError
from app.models.gsc_connection import GscConnection
from app.models.user import User
from app.providers.google import gsc_client

router = APIRouter(prefix="/gsc", tags=["GSC"])


class GscStatusOut(BaseModel):
    configured: bool
    connected: bool
    google_email: str | None = None
    site_url: str | None = None
    updated_at: str | None = None


class GscSiteOut(BaseModel):
    site_url: str
    permission_level: str | None = None


class SelectSiteIn(BaseModel):
    site_url: str = Field(..., min_length=1, max_length=500)


def _status_for(db: Session, site_id: int) -> GscStatusOut:
    conn = db.query(GscConnection).filter(GscConnection.site_id == site_id).first()
    connected = bool(conn and conn.refresh_token and conn.access_token)
    return GscStatusOut(
        configured=gsc_client.is_gsc_configured(),
        connected=connected,
        google_email=conn.google_email if conn else None,
        site_url=conn.site_url if conn else None,
        updated_at=conn.updated_at.isoformat() if conn and conn.updated_at else None,
    )


@router.get("/status", response_model=GscStatusOut)
@api_route
def gsc_status(
    db: Session = Depends(get_db),
    ctx: SiteContext = Depends(get_site_context),
    _: User = Depends(get_current_user),
) -> GscStatusOut:
    return _status_for(db, ctx.site.id)


@router.get("/oauth/start")
@api_route
def oauth_start(
    db: Session = Depends(get_db),
    ctx: SiteContext = Depends(require_site_write),
    user: User = Depends(get_current_user),
) -> dict[str, str]:
    if not gsc_client.is_gsc_configured():
        raise ValidationError("未配置 Google OAuth", code="GSC_NOT_CONFIGURED")
    auth_url = gsc_client.get_authorization_url(db, user.id, ctx.site.id)
    return {"auth_url": auth_url}


@router.get("/oauth/callback")
def oauth_callback(
    code: str = Query(...),
    state: str = Query(...),
    db: Session = Depends(get_db),
):
    try:
        user_id, site_id, code_verifier = gsc_client.consume_oauth_session(db, state)
        tokens = gsc_client.exchange_code_for_tokens(code, code_verifier)
        email = gsc_client.fetch_google_email(tokens["access_token"])
        gsc_client.save_connection_tokens(
            db,
            site_id,
            user_id,
            access_token=tokens["access_token"],
            refresh_token=tokens.get("refresh_token"),
            token_expires_at=tokens["token_expires_at"],
            google_email=email,
        )
        return RedirectResponse(gsc_client.frontend_redirect({"gsc": "connected"}))
    except Exception:  # noqa: BLE001
        return RedirectResponse(gsc_client.frontend_redirect({"gsc": "error"}))


@router.delete("/disconnect")
@api_route
def disconnect(
    db: Session = Depends(get_db),
    ctx: SiteContext = Depends(require_site_write),
    _: User = Depends(get_current_user),
) -> dict[str, bool]:
    gsc_client.disconnect_connection(db, ctx.site.id)
    return {"ok": True}


@router.get("/sites", response_model=list[GscSiteOut])
@api_route
def list_sites(
    db: Session = Depends(get_db),
    ctx: SiteContext = Depends(get_site_context),
    _: User = Depends(get_current_user),
) -> list[GscSiteOut]:
    rows = gsc_client.list_sites(db, ctx.site.id)
    return [GscSiteOut(**r) for r in rows]


@router.put("/sites/active", response_model=GscStatusOut)
@api_route
def select_active_site(
    payload: SelectSiteIn,
    db: Session = Depends(get_db),
    ctx: SiteContext = Depends(require_site_write),
    _: User = Depends(get_current_user),
) -> GscStatusOut:
    sites = {s["site_url"] for s in gsc_client.list_sites(db, ctx.site.id)}
    if payload.site_url not in sites:
        raise ValidationError("无权选择该 GSC 属性", code="GSC_SITE_FORBIDDEN")
    conn = db.query(GscConnection).filter(GscConnection.site_id == ctx.site.id).first()
    if not conn:
        raise ValidationError("尚未连接 GSC", code="GSC_NOT_CONNECTED")
    conn.site_url = payload.site_url
    db.commit()

    # 选好属性后自动增强
    from app.core.config import settings

    if settings.is_dev:
        from app.domains.sites.onboarding_service import OnboardingService

        OnboardingService.enhance_after_gsc(db, ctx.site.id)
    else:
        try:
            from app.tasks.platform_tasks import enhance_gsc

            enhance_gsc.delay(ctx.site.id)
        except Exception:
            from app.domains.sites.onboarding_service import OnboardingService

            OnboardingService.enhance_after_gsc(db, ctx.site.id)

    return _status_for(db, ctx.site.id)


@router.get("/analytics/summary")
@api_route
def analytics_summary(
    days: int = Query(default=28, ge=1, le=365),
    db: Session = Depends(get_db),
    ctx: SiteContext = Depends(get_site_context),
    _: User = Depends(get_current_user),
) -> dict:
    return gsc_client.query_summary(db, ctx.site.id, days=days)


@router.get("/analytics")
@api_route
def analytics_detail(
    days: int = Query(default=28, ge=1, le=365),
    dimension: str = Query(default="query"),
    row_limit: int = Query(default=100, ge=1, le=1000),
    db: Session = Depends(get_db),
    ctx: SiteContext = Depends(get_site_context),
    _: User = Depends(get_current_user),
) -> dict:
    return gsc_client.query_search_analytics(
        db, ctx.site.id, days=days, dimension=dimension, row_limit=row_limit
    )
