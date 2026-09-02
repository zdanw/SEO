"""Reddit OAuth helpers (mirrors gsc_client pattern)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlencode

from jose import JWTError, jwt
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.social import SocialAccount
from app.services.reddit_client import (
    RedditApiClient,
    RedditApiError,
    build_reddit_auth_url,
    exchange_reddit_code,
    is_reddit_configured,
)

OAUTH_STATE_PURPOSE = "reddit_oauth"
REDDIT_SCOPES = "identity read submit"


class RedditNotConfiguredError(Exception):
    pass


def create_oauth_state(user_id: int, site_id: int) -> str:
    expire = datetime.now(timezone.utc) + timedelta(minutes=10)
    payload = {
        "exp": expire,
        "sub": str(user_id),
        "site_id": site_id,
        "purpose": OAUTH_STATE_PURPOSE,
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm="HS256")


def verify_oauth_state(state: str) -> tuple[int, int]:
    try:
        payload = jwt.decode(state, settings.SECRET_KEY, algorithms=["HS256"])
    except JWTError as exc:
        raise ValueError("无效的 OAuth state") from exc
    if payload.get("purpose") != OAUTH_STATE_PURPOSE:
        raise ValueError("无效的 OAuth state")
    site_id = payload.get("site_id")
    if not site_id:
        raise ValueError("OAuth 会话已过期，请重新连接")
    return int(payload["sub"]), int(site_id)


def get_authorization_url(user_id: int, site_id: int) -> str:
    if not is_reddit_configured():
        raise RedditNotConfiguredError("未配置 REDDIT_CLIENT_ID / REDDIT_CLIENT_SECRET")
    state = create_oauth_state(user_id, site_id)
    return build_reddit_auth_url(state)


def upsert_reddit_account(
    db: Session,
    user_id: int,
    site_id: int,
    tokens: dict[str, Any],
    username: str,
) -> SocialAccount:
    acc = (
        db.query(SocialAccount)
        .filter(
            SocialAccount.site_id == site_id,
            SocialAccount.platform == "reddit",
            SocialAccount.account_name == f"u/{username}",
        )
        .first()
    )
    expires_in = int(tokens.get("expires_in", 3600))
    token_expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in)
    if not acc:
        acc = SocialAccount(
            user_id=user_id,
            site_id=site_id,
            platform="reddit",
            account_name=f"u/{username}",
            is_active=True,
        )
        db.add(acc)
    acc.access_token = tokens["access_token"]
    if tokens.get("refresh_token"):
        acc.refresh_token = tokens["refresh_token"]
    acc.token_expires_at = token_expires_at
    db.commit()
    db.refresh(acc)
    return acc


def complete_oauth_callback(db: Session, code: str, state: str) -> SocialAccount:
    user_id, site_id = verify_oauth_state(state)
    tokens = exchange_reddit_code(code)
    client = RedditApiClient(account_id=0, access_token=tokens["access_token"])
    me = client.get_me()
    username = me.get("name", "unknown")
    return upsert_reddit_account(db, user_id, site_id, tokens, username)


def frontend_redirect(params: dict[str, str]) -> str:
    base = settings.FRONTEND_URL.rstrip("/")
    query = urlencode(params)
    return f"{base}/social?{query}"


def list_reddit_accounts(db: Session, site_id: int) -> list[SocialAccount]:
    return (
        db.query(SocialAccount)
        .filter(
            SocialAccount.site_id == site_id,
            SocialAccount.platform == "reddit",
            SocialAccount.is_active == True,
        )
        .order_by(SocialAccount.created_at.desc())
        .all()
    )


def get_site_account(db: Session, site_id: int, account_id: int) -> SocialAccount | None:
    return (
        db.query(SocialAccount)
        .filter(
            SocialAccount.id == account_id,
            SocialAccount.site_id == site_id,
            SocialAccount.platform == "reddit",
        )
        .first()
    )
