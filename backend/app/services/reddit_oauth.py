"""Reddit 账号同步：从 Zernio Key 池拉取已绑定账号。"""
from __future__ import annotations

from urllib.parse import urlencode

from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.social import SocialAccount
from app.services.zernio_client import ZernioError, get_zernio_client
from app.services.zernio_keys import iter_sync_clients, list_enabled_keys


class RedditNotConfiguredError(Exception):
    pass


def frontend_redirect(params: dict[str, str]) -> str:
    base = settings.FRONTEND_URL.rstrip("/")
    query = urlencode(params)
    return f"{base}/social?{query}"


def callback_redirect_url() -> str:
    return f"{settings.FRONTEND_URL.rstrip('/')}/social"


def get_authorization_url(user_id: int, site_id: int) -> str:
    client = get_zernio_client()
    try:
        return client.get_connect_url(callback_redirect_url())
    except ZernioError as exc:
        raise RedditNotConfiguredError(str(exc)) from exc


def _display_name(raw: dict) -> str:
    username = str(raw.get("username") or raw.get("displayName") or "reddit").strip()
    if username.startswith("u/"):
        return username
    if username.startswith("@"):
        username = username[1:]
    return f"u/{username}" if username else "u/unknown"


def _cfg(account: SocialAccount) -> dict:
    return dict(account.config or {})


def _ids_of(account: SocialAccount) -> tuple[int | None, str | None]:
    cfg = account.config or {}
    raw_kid = cfg.get("zernio_key_id")
    kid: int | None
    if raw_kid in (None, ""):
        kid = None
    else:
        try:
            kid = int(raw_kid)
        except (TypeError, ValueError):
            kid = None
    zid = cfg.get("zernio_account_id")
    return kid, str(zid) if zid else None


def _find_by_zernio_ids(
    db: Session,
    site_id: int,
    zernio_key_id: int | None,
    zernio_account_id: str,
) -> SocialAccount | None:
    for existing in (
        db.query(SocialAccount)
        .filter(SocialAccount.site_id == site_id, SocialAccount.platform == "reddit")
        .all()
    ):
        kid, zid = _ids_of(existing)
        if zid == zernio_account_id and kid == zernio_key_id:
            return existing
    return None


def display_reddit_name(name: str | None) -> str | None:
    """账号列只显示 Reddit 用户名，不附带 Key 备注。"""
    if not name:
        return name
    return name.split(" · ", 1)[0]


def upsert_from_zernio(
    db: Session,
    user_id: int,
    site_id: int,
    zernio_account_id: str,
    username: str,
    extra: dict | None = None,
    zernio_key_id: int | None = None,
    key_label: str | None = None,
) -> SocialAccount:
    del key_label  # 备注只存在 Key 列，不写入账号名
    name = username if username.startswith("u/") else f"u/{username.lstrip('@')}"
    acc = _find_by_zernio_ids(db, site_id, zernio_key_id, zernio_account_id)
    if not acc:
        by_name = (
            db.query(SocialAccount)
            .filter(
                SocialAccount.site_id == site_id,
                SocialAccount.platform == "reddit",
                SocialAccount.account_name == name,
            )
            .first()
        )
        if by_name:
            existing_kid, existing_zid = _ids_of(by_name)
            reusable = existing_zid in (None, zernio_account_id) and existing_kid in (None, zernio_key_id)
            if reusable:
                acc = by_name
    if not acc:
        acc = SocialAccount(
            user_id=user_id,
            site_id=site_id,
            platform="reddit",
            account_name=name,
            is_active=True,
        )
        db.add(acc)
    acc.account_name = name
    acc.is_active = True
    acc.access_token = zernio_account_id
    cfg = _cfg(acc)
    cfg["zernio_account_id"] = zernio_account_id
    cfg["zernio_key_id"] = zernio_key_id
    if extra:
        cfg.update(extra)
    acc.config = cfg
    db.commit()
    db.refresh(acc)
    return acc


def sync_zernio_accounts(
    db: Session, user_id: int, site_id: int
) -> tuple[list[SocialAccount], list[str]]:
    clients = iter_sync_clients(db)
    if not clients:
        raise ZernioError("请先在「社交账号」页面添加并启用 Zernio API Key")

    errors: list[str] = []
    seen: set[tuple[int | None, str]] = set()
    ok_keys: set[int | None] = set()
    any_success = False
    for key, client in clients:
        kid = key.id
        label = key.label
        try:
            remote = client.list_reddit_accounts()
        except ZernioError as exc:
            errors.append(f"{label}: {exc}")
            continue
        any_success = True
        ok_keys.add(kid)
        for raw in remote:
            zid = str(raw.get("_id") or raw.get("id") or "")
            if not zid:
                continue
            seen.add((kid, zid))
            extra = {"zernio_username": raw.get("username"), "zernio_key_id": kid}
            upsert_from_zernio(
                db,
                user_id,
                site_id,
                zid,
                _display_name(raw),
                extra=extra,
                zernio_key_id=kid,
                key_label=label,
            )
    if not any_success:
        raise ZernioError(errors[0] if errors else "同步失败")

    existing = (
        db.query(SocialAccount)
        .filter(SocialAccount.site_id == site_id, SocialAccount.platform == "reddit")
        .all()
    )
    for acc in existing:
        kid, zid = _ids_of(acc)
        if not zid:
            if any_success:
                acc.is_active = False
            continue
        if kid not in ok_keys:
            continue
        if (kid, zid) not in seen:
            acc.is_active = False
    db.commit()
    return list_reddit_accounts(db, site_id), errors


def deactivate_orphan_reddit_accounts(db: Session, site_id: int) -> int:
    """停用未绑定启用中 Zernio Key 的 Reddit 账号（含历史 env 同步的空 key_id）。"""
    enabled_ids = {k.id for k in list_enabled_keys(db)}
    accounts = (
        db.query(SocialAccount)
        .filter(
            SocialAccount.site_id == site_id,
            SocialAccount.platform == "reddit",
            SocialAccount.is_active == True,
        )
        .all()
    )
    changed = 0
    for acc in accounts:
        kid, _zid = _ids_of(acc)
        if kid is None or kid not in enabled_ids:
            acc.is_active = False
            changed += 1
    if changed:
        db.commit()
    return changed


def list_reddit_accounts(db: Session, site_id: int) -> list[SocialAccount]:
    deactivate_orphan_reddit_accounts(db, site_id)
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
