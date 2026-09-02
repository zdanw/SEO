"""Google Search Console API 客户端与 OAuth 辅助（按客户站点）。"""
from __future__ import annotations

import logging
import secrets
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional
from urllib.parse import urlencode

import httpx
from google.auth.transport.requests import Request as GoogleAuthRequest
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from jose import JWTError, jwt
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.gsc_connection import GscConnection

logger = logging.getLogger(__name__)

GSC_SCOPES = ["https://www.googleapis.com/auth/webmasters.readonly"]
OAUTH_STATE_PURPOSE = "gsc_oauth"
GSC_DATA_LAG_DAYS = 2


class GscNotConfiguredError(Exception):
    """未配置 Google OAuth 凭证。"""


class GscNotConnectedError(Exception):
    """站点尚未连接 GSC。"""


class GscSiteNotSelectedError(Exception):
    """未选择 GSC 属性站点。"""


def is_gsc_configured() -> bool:
    return bool(settings.GOOGLE_CLIENT_ID and settings.GOOGLE_CLIENT_SECRET)


def _client_config() -> dict[str, Any]:
    if not is_gsc_configured():
        raise GscNotConfiguredError("未配置 GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET")
    return {
        "web": {
            "client_id": settings.GOOGLE_CLIENT_ID,
            "client_secret": settings.GOOGLE_CLIENT_SECRET,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [settings.GOOGLE_REDIRECT_URI],
        }
    }


def create_oauth_state(user_id: int, site_id: int, nonce: str) -> str:
    expire = datetime.now(timezone.utc) + timedelta(minutes=10)
    payload = {
        "exp": expire,
        "sub": str(user_id),
        "site_id": site_id,
        "purpose": OAUTH_STATE_PURPOSE,
        "nonce": nonce,
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm="HS256")


def verify_oauth_state(state: str) -> tuple[int, int, str]:
    try:
        payload = jwt.decode(state, settings.SECRET_KEY, algorithms=["HS256"])
    except JWTError as exc:
        raise ValueError("无效的 OAuth state") from exc
    if payload.get("purpose") != OAUTH_STATE_PURPOSE:
        raise ValueError("无效的 OAuth state")
    nonce = payload.get("nonce")
    site_id = payload.get("site_id")
    if not nonce or not site_id:
        raise ValueError("OAuth 会话已过期，请重新点击连接")
    return int(payload["sub"]), int(site_id), str(nonce)


def prepare_oauth_session(db: Session, user_id: int, site_id: int) -> str:
    """生成授权 URL，并将 PKCE code_verifier 暂存到数据库。"""
    code_verifier = secrets.token_urlsafe(64)[:128]
    nonce = secrets.token_urlsafe(32)
    conn = get_or_create_connection(db, site_id, user_id)
    conn.oauth_code_verifier = code_verifier
    conn.oauth_state_nonce = nonce
    conn.updated_at = datetime.utcnow()
    db.commit()

    flow = build_oauth_flow(code_verifier=code_verifier)
    state = create_oauth_state(user_id, site_id, nonce)
    auth_url, _ = flow.authorization_url(
        access_type="offline",
        prompt="consent",
        state=state,
    )
    return auth_url


def consume_oauth_session(db: Session, state: str) -> tuple[int, int, str]:
    """校验 state 并取出暂存的 code_verifier（一次性使用）。"""
    user_id, site_id, nonce = verify_oauth_state(state)
    conn = db.query(GscConnection).filter(GscConnection.site_id == site_id).first()
    if not conn or not conn.oauth_code_verifier or conn.oauth_state_nonce != nonce:
        raise ValueError("OAuth 会话已过期，请重新点击连接 Google 账号")
    code_verifier = conn.oauth_code_verifier
    conn.oauth_code_verifier = None
    conn.oauth_state_nonce = None
    conn.updated_at = datetime.utcnow()
    db.commit()
    return user_id, site_id, code_verifier


def build_oauth_flow(*, code_verifier: str | None = None) -> Flow:
    kwargs: dict[str, Any] = {}
    if code_verifier:
        kwargs["code_verifier"] = code_verifier
    else:
        kwargs["autogenerate_code_verifier"] = True
    return Flow.from_client_config(
        _client_config(),
        scopes=GSC_SCOPES,
        redirect_uri=settings.GOOGLE_REDIRECT_URI,
        **kwargs,
    )


def get_authorization_url(db: Session, user_id: int, site_id: int) -> str:
    return prepare_oauth_session(db, user_id, site_id)


def exchange_code_for_tokens(code: str, code_verifier: str) -> dict[str, Any]:
    resp = httpx.post(
        "https://oauth2.googleapis.com/token",
        data={
            "code": code,
            "client_id": settings.GOOGLE_CLIENT_ID,
            "client_secret": settings.GOOGLE_CLIENT_SECRET,
            "redirect_uri": settings.GOOGLE_REDIRECT_URI,
            "grant_type": "authorization_code",
            "code_verifier": code_verifier,
        },
        timeout=30,
    )
    if resp.status_code >= 400:
        detail = resp.text[:300]
        raise RuntimeError(f"Token exchange failed ({resp.status_code}): {detail}")
    data = resp.json()
    expires_in = int(data.get("expires_in", 3600))
    token_expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in)
    return {
        "access_token": data["access_token"],
        "refresh_token": data.get("refresh_token"),
        "token_expires_at": token_expires_at,
    }


def fetch_google_email(access_token: str) -> Optional[str]:
    try:
        resp = httpx.get(
            "https://www.googleapis.com/oauth2/v2/userinfo",
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=15,
        )
        resp.raise_for_status()
        return resp.json().get("email")
    except Exception as exc:
        logger.warning("获取 Google 用户邮箱失败: %s", exc)
        return None


def get_or_create_connection(db: Session, site_id: int, user_id: int) -> GscConnection:
    conn = db.query(GscConnection).filter(GscConnection.site_id == site_id).first()
    if not conn:
        conn = GscConnection(site_id=site_id, user_id=user_id)
        db.add(conn)
        db.flush()
    return conn


def save_connection_tokens(
    db: Session,
    site_id: int,
    user_id: int,
    *,
    access_token: str,
    refresh_token: Optional[str],
    token_expires_at: Optional[datetime],
    google_email: Optional[str],
) -> GscConnection:
    conn = get_or_create_connection(db, site_id, user_id)
    conn.access_token = access_token
    if refresh_token:
        conn.refresh_token = refresh_token
    conn.token_expires_at = token_expires_at
    if google_email:
        conn.google_email = google_email
    conn.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(conn)
    return conn


def disconnect_connection(db: Session, site_id: int) -> None:
    conn = db.query(GscConnection).filter(GscConnection.site_id == site_id).first()
    if conn:
        conn.oauth_code_verifier = None
        conn.oauth_state_nonce = None
        db.delete(conn)
        db.commit()


def _build_credentials(conn: GscConnection) -> Credentials:
    if not conn.access_token:
        raise GscNotConnectedError("尚未连接 Google Search Console")
    creds = Credentials(
        token=conn.access_token,
        refresh_token=conn.refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=settings.GOOGLE_CLIENT_ID,
        client_secret=settings.GOOGLE_CLIENT_SECRET,
        scopes=GSC_SCOPES,
    )
    if creds.expired and creds.refresh_token:
        creds.refresh(GoogleAuthRequest())
        conn.access_token = creds.token
        if creds.expiry:
            conn.token_expires_at = creds.expiry.replace(tzinfo=timezone.utc)
        conn.updated_at = datetime.utcnow()
    return creds


def get_gsc_service(db: Session, site_id: int):
    conn = db.query(GscConnection).filter(GscConnection.site_id == site_id).first()
    if not conn or not conn.access_token:
        raise GscNotConnectedError("尚未连接 Google Search Console")
    creds = _build_credentials(conn)
    db.commit()
    return build("searchconsole", "v1", credentials=creds, cache_discovery=False), conn


def list_sites(db: Session, site_id: int) -> list[dict[str, str]]:
    service, _ = get_gsc_service(db, site_id)
    result = service.sites().list().execute()
    entries = result.get("siteEntry", [])
    sites: list[dict[str, str]] = []
    for entry in entries:
        site_url = entry.get("siteUrl")
        if site_url:
            sites.append(
                {
                    "site_url": site_url,
                    "permission_level": entry.get("permissionLevel", ""),
                }
            )
    sites.sort(key=lambda s: s["site_url"])
    return sites


def resolve_date_range(days: int) -> tuple[str, str]:
    end = date.today() - timedelta(days=GSC_DATA_LAG_DAYS)
    start = end - timedelta(days=max(days - 1, 0))
    return start.isoformat(), end.isoformat()


def query_search_analytics(
    db: Session,
    site_id: int,
    *,
    days: int = 28,
    dimension: str = "query",
    row_limit: int = 100,
) -> dict[str, Any]:
    service, conn = get_gsc_service(db, site_id)
    if not conn.site_url:
        raise GscSiteNotSelectedError("请先选择 Search Console 站点")

    start_date, end_date = resolve_date_range(days)
    body: dict[str, Any] = {
        "startDate": start_date,
        "endDate": end_date,
        "rowLimit": min(max(row_limit, 1), 25000),
        "dimensions": ["page" if dimension == "page" else "query"],
    }

    response = service.searchanalytics().query(
        siteUrl=conn.site_url,
        body=body,
    ).execute()

    rows: list[dict[str, Any]] = []
    for row in response.get("rows", []):
        keys = row.get("keys", [])
        key = keys[0] if keys else ""
        rows.append(
            {
                "key": key,
                "clicks": int(row.get("clicks", 0)),
                "impressions": int(row.get("impressions", 0)),
                "ctr": float(row.get("ctr", 0)),
                "position": float(row.get("position", 0)),
            }
        )

    return {
        "start_date": start_date,
        "end_date": end_date,
        "site_url": conn.site_url,
        "dimension": dimension,
        "rows": rows,
    }


def query_summary(db: Session, site_id: int, *, days: int = 28) -> dict[str, Any]:
    service, conn = get_gsc_service(db, site_id)
    if not conn.site_url:
        raise GscSiteNotSelectedError("请先选择 Search Console 站点")

    start_date, end_date = resolve_date_range(days)
    response = service.searchanalytics().query(
        siteUrl=conn.site_url,
        body={"startDate": start_date, "endDate": end_date},
    ).execute()

    rows = response.get("rows", [])
    if not rows:
        return {
            "start_date": start_date,
            "end_date": end_date,
            "site_url": conn.site_url,
            "clicks": 0,
            "impressions": 0,
            "ctr": 0.0,
            "position": 0.0,
        }

    row = rows[0]
    return {
        "start_date": start_date,
        "end_date": end_date,
        "site_url": conn.site_url,
        "clicks": int(row.get("clicks", 0)),
        "impressions": int(row.get("impressions", 0)),
        "ctr": float(row.get("ctr", 0)),
        "position": float(row.get("position", 0)),
    }


def frontend_redirect(params: dict[str, str]) -> str:
    base = settings.FRONTEND_URL.rstrip("/")
    query = urlencode(params)
    return f"{base}/search-console?{query}"
