"""Google Search Console 集成 API（按客户站点）。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.api.deps import SiteContext, get_current_user, get_site_context, require_site_write
from app.core.database import get_db
from app.models.gsc_connection import GscConnection
from app.models.user import User
from app.schemas.gsc import (
    GscAnalyticsOut,
    GscOAuthStartOut,
    GscSiteOut,
    GscSiteSelectIn,
    GscStatusOut,
    GscSummaryOut,
)
from app.services import gsc_client
from app.services.gsc_client import (
    GscNotConfiguredError,
    GscNotConnectedError,
    GscSiteNotSelectedError,
)

router = APIRouter()


def _get_connection(db: Session, site_id: int) -> GscConnection | None:
    return db.query(GscConnection).filter(GscConnection.site_id == site_id).first()


@router.get("/status", response_model=GscStatusOut)
def get_status(
    db: Session = Depends(get_db),
    ctx: SiteContext = Depends(get_site_context),
) -> GscStatusOut:
    conn = _get_connection(db, ctx.site.id)
    return GscStatusOut(
        configured=gsc_client.is_gsc_configured(),
        connected=bool(conn and conn.access_token),
        google_email=conn.google_email if conn else None,
        site_url=conn.site_url if conn else None,
        updated_at=conn.updated_at if conn else None,
    )


@router.get("/oauth/start", response_model=GscOAuthStartOut)
def oauth_start(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    ctx: SiteContext = Depends(require_site_write),
) -> GscOAuthStartOut:
    try:
        auth_url = gsc_client.get_authorization_url(db, current_user.id, ctx.site.id)
    except GscNotConfiguredError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return GscOAuthStartOut(auth_url=auth_url)


@router.get("/oauth/callback")
def oauth_callback(
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    db: Session = Depends(get_db),
):
    if error:
        return RedirectResponse(
            gsc_client.frontend_redirect({"gsc": "error", "message": error})
        )
    if not code or not state:
        return RedirectResponse(
            gsc_client.frontend_redirect({"gsc": "error", "message": "missing_code"})
        )
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
            token_expires_at=tokens.get("token_expires_at"),
            google_email=email,
        )
        return RedirectResponse(
            gsc_client.frontend_redirect({"gsc": "connected", "site_id": str(site_id)})
        )
    except Exception as exc:
        return RedirectResponse(
            gsc_client.frontend_redirect({"gsc": "error", "message": str(exc)[:200]})
        )


@router.delete("/disconnect", status_code=status.HTTP_204_NO_CONTENT, response_class=Response)
def disconnect(
    db: Session = Depends(get_db),
    ctx: SiteContext = Depends(require_site_write),
) -> Response:
    gsc_client.disconnect_connection(db, ctx.site.id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/sites", response_model=list[GscSiteOut])
def list_gsc_sites(
    db: Session = Depends(get_db),
    ctx: SiteContext = Depends(get_site_context),
) -> list[GscSiteOut]:
    try:
        sites = gsc_client.list_sites(db, ctx.site.id)
    except GscNotConfiguredError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except GscNotConnectedError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"GSC API 调用失败: {exc}") from exc
    return [GscSiteOut(**site) for site in sites]


@router.put("/sites/active", response_model=GscStatusOut)
def select_active_site(
    payload: GscSiteSelectIn,
    db: Session = Depends(get_db),
    ctx: SiteContext = Depends(require_site_write),
) -> GscStatusOut:
    conn = _get_connection(db, ctx.site.id)
    if not conn or not conn.access_token:
        raise HTTPException(status_code=400, detail="尚未连接 Google Search Console")

    try:
        sites = gsc_client.list_sites(db, ctx.site.id)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"GSC API 调用失败: {exc}") from exc

    allowed = {s["site_url"] for s in sites}
    if payload.site_url not in allowed:
        raise HTTPException(status_code=400, detail="无权访问该 GSC 属性")

    conn.site_url = payload.site_url
    db.commit()
    db.refresh(conn)
    return GscStatusOut(
        configured=gsc_client.is_gsc_configured(),
        connected=True,
        google_email=conn.google_email,
        site_url=conn.site_url,
        updated_at=conn.updated_at,
    )


@router.get("/analytics/summary", response_model=GscSummaryOut)
def analytics_summary(
    days: int = Query(28, ge=1, le=90),
    db: Session = Depends(get_db),
    ctx: SiteContext = Depends(get_site_context),
) -> GscSummaryOut:
    try:
        data = gsc_client.query_summary(db, ctx.site.id, days=days)
    except GscNotConfiguredError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except GscNotConnectedError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except GscSiteNotSelectedError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"GSC API 调用失败: {exc}") from exc
    return GscSummaryOut(**data)


@router.get("/analytics", response_model=GscAnalyticsOut)
def analytics_detail(
    days: int = Query(28, ge=1, le=90),
    dimension: str = Query("query", pattern="^(query|page)$"),
    row_limit: int = Query(100, ge=1, le=1000),
    db: Session = Depends(get_db),
    ctx: SiteContext = Depends(get_site_context),
) -> GscAnalyticsOut:
    try:
        data = gsc_client.query_search_analytics(
            db,
            ctx.site.id,
            days=days,
            dimension=dimension,
            row_limit=row_limit,
        )
    except GscNotConfiguredError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except GscNotConnectedError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except GscSiteNotSelectedError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"GSC API 调用失败: {exc}") from exc
    return GscAnalyticsOut(**data)
