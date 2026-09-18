"""Shared Zernio API key pool helpers (DB only; no env fallback)."""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.zernio_key import ZernioApiKey
from app.services.zernio_client import ZernioClient


def mask_api_key(secret: str) -> str:
    s = (secret or "").strip()
    if not s:
        return ""
    if len(s) <= 8:
        return "****"
    return f"{s[:4]}…{s[-4:]}"


def list_enabled_keys(db: Session) -> list[ZernioApiKey]:
    return (
        db.query(ZernioApiKey)
        .filter(ZernioApiKey.is_enabled.is_(True))
        .order_by(ZernioApiKey.id.asc())
        .all()
    )


def list_all_keys(db: Session) -> list[ZernioApiKey]:
    return db.query(ZernioApiKey).order_by(ZernioApiKey.id.asc()).all()


def is_zernio_ready(db: Session) -> bool:
    return bool(list_enabled_keys(db))


def client_for_key(key: ZernioApiKey, account_id: int = 0) -> ZernioClient:
    return ZernioClient(
        account_id=account_id,
        api_key=key.api_key,
        profile_id=key.profile_id,
    )


def iter_sync_clients(db: Session) -> list[tuple[ZernioApiKey, ZernioClient]]:
    return [(k, client_for_key(k)) for k in list_enabled_keys(db)]


def get_enabled_key(db: Session, key_id: int) -> ZernioApiKey | None:
    return (
        db.query(ZernioApiKey)
        .filter(ZernioApiKey.id == key_id, ZernioApiKey.is_enabled.is_(True))
        .first()
    )
