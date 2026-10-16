"""Reddit helpers: URL parsing + Zernio-backed publish/search/comment."""
from __future__ import annotations

import re
from typing import Any

from sqlalchemy.orm import Session, object_session
from sqlalchemy.orm.exc import UnmappedInstanceError

from app.services.zernio_client import ZernioError, get_zernio_client, is_zernio_configured

_REDDIT_POST_RE = re.compile(
    r"reddit\.com/r/(?P<subreddit>[^/]+)/comments/(?P<post_id>[a-z0-9]+)",
    re.IGNORECASE,
)


class RedditApiError(RuntimeError):
    """Reddit / Zernio call failed."""

    def __init__(
        self,
        message: str,
        status_code: int | None = None,
        retry_after: int | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.retry_after = retry_after


class RedditActionRetryable(RedditApiError):
    """Reddit 操作遇限流/熔断，已重置为待重试状态，由任务层延迟自动重试。

    继承 RedditApiError：手动发布的 API 路径未捕获时也能按 429 正确返回。
    """

    def __init__(self, message: str, status_code: int, retry_after: int, attempt: int) -> None:
        super().__init__(message, status_code=status_code, retry_after=retry_after)
        self.attempt = attempt


# 熔断器 open_duration=600s（reddit 平台），503 等 half-open 需要 ≥600s
_RETRY_DEFAULTS = {429: 60, 503: 620}
_RETRY_CAP_SECONDS = 900


def reddit_retry_delay(exc: RedditApiError, cap: int = _RETRY_CAP_SECONDS) -> int | None:
    """瞬时限流(429)/熔断(503)返回建议等待秒数；其余错误返回 None（不自动重试）。

    - 429：本地令牌桶拒绝（retry_after=10）或上游限流 → clamp 至 30s 起
    - 503：熔断器打开 → 默认等 620s（open 600s 后进入半开）
    - 5xx 其他（500/502/504）：语义不明（超时可能已生效），不自动重试防重复
    """
    code = getattr(exc, "status_code", None)
    default = _RETRY_DEFAULTS.get(code)
    if default is None:
        return None
    wait = getattr(exc, "retry_after", None) or default
    return int(max(30, min(wait, cap)))


def parse_reddit_post_url(url: str) -> tuple[str, str]:
    """Parse a Reddit post URL into (thing_id, subreddit)."""
    m = _REDDIT_POST_RE.search(url.strip())
    if not m:
        raise ValueError(f"Invalid Reddit post URL: {url}")
    return f"t3_{m.group('post_id')}", m.group("subreddit")


def is_reddit_configured() -> bool:
    """True when Zernio API key is set (direct Reddit OAuth is no longer used)."""
    return is_zernio_configured()


def normalize_subreddit(name: str) -> str:
    return name.strip().removeprefix("r/").removeprefix("/")


def zernio_account_id_of(account) -> str | None:
    cfg = account.config if getattr(account, "config", None) else None
    if isinstance(cfg, dict) and cfg.get("zernio_account_id"):
        return str(cfg["zernio_account_id"])
    token = getattr(account, "access_token", None)
    return str(token) if token else None


class RedditApiClient:
    """Publish/search/comment via Zernio, with mock fallback when unconfigured."""

    def __init__(
        self,
        account_id: int,
        zernio_account_id: str | None = None,
        access_token: str | None = None,
        refresh_token: str | None = None,
        api_key: str | None = None,
        profile_id: str | None = None,
    ) -> None:
        self.account_id = account_id
        self.zernio_account_id = zernio_account_id or access_token
        if api_key is None and profile_id is None:
            self._zernio = get_zernio_client(account_id)
        else:
            self._zernio = get_zernio_client(account_id, api_key=api_key, profile_id=profile_id)

    def _require_zernio_id(self) -> str:
        if self.zernio_account_id:
            return self.zernio_account_id
        if self._zernio._mock_mode:
            return "mock_zernio_reddit"
        raise RedditApiError("账号未绑定 Zernio Reddit accountId，请先同步账号", status_code=400)

    def _call(self, fn, *args, **kwargs) -> Any:
        try:
            return fn(*args, **kwargs)
        except ZernioError as exc:
            raise RedditApiError(
                str(exc),
                status_code=exc.status_code,
                retry_after=exc.retry_after,
            ) from exc

    def submit_post(self, subreddit: str, title: str, body: str) -> dict[str, Any]:
        sr = normalize_subreddit(subreddit)
        return self._call(self._zernio.submit_post, self._require_zernio_id(), sr, title, body)

    def submit_comment(self, thing_id: str, body: str, subreddit: str | None = None) -> dict[str, Any]:
        return self._call(
            self._zernio.submit_comment,
            self._require_zernio_id(),
            thing_id,
            body,
            subreddit,
        )

    def get_post_context(self, thing_id: str, subreddit: str = "") -> dict[str, Any]:
        return self._call(
            self._zernio.get_post_context,
            self._require_zernio_id(),
            thing_id,
            normalize_subreddit(subreddit) if subreddit else "",
        )

    def get_post_by_id(self, thing_id: str, subreddit: str = "") -> dict[str, Any] | None:
        """按帖子 ID 精确取帖（含 score/num_comments）；取不到返回 None。"""
        return self._call(
            self._zernio.get_post_by_id,
            self._require_zernio_id(),
            thing_id,
            normalize_subreddit(subreddit) if subreddit else "",
        )

    def search_posts(self, subreddit: str, keyword: str, limit: int = 10) -> list[dict[str, Any]]:
        sr = normalize_subreddit(subreddit)
        return self._call(self._zernio.search_posts, self._require_zernio_id(), sr, keyword, limit)

    def list_feed(self, subreddit: str, limit: int = 10) -> list[dict[str, Any]]:
        sr = normalize_subreddit(subreddit)
        return self._call(self._zernio.list_feed, self._require_zernio_id(), sr, limit)

    def get_subreddit_rules(self, subreddit: str) -> dict[str, Any]:
        sr = normalize_subreddit(subreddit)
        return self._call(self._zernio.get_subreddit_rules, self._require_zernio_id(), sr)

    def vote(self, thing_id: str, direction: int = 1) -> dict[str, Any]:
        return self._call(self._zernio.vote_reddit_thing, self._require_zernio_id(), thing_id, direction)

    def list_post_comments(self, thing_id: str, subreddit: str = "", limit: int = 8) -> list[dict[str, Any]]:
        return self._call(
            self._zernio.list_post_comments,
            self._require_zernio_id(),
            thing_id,
            normalize_subreddit(subreddit) if subreddit else "",
            limit,
        )


def resolve_zernio_api_credentials(
    config: dict | None,
    db: Session | None = None,
    site_id: int | None = None,
) -> tuple[str | None, str | None]:
    """Return (api_key, profile_id) from the account's linked zernio_api_keys row.

    Missing key id → (None, None) so ZernioClient runs in mock mode.
    """
    cfg = config if isinstance(config, dict) else {}
    raw_kid = cfg.get("zernio_key_id")
    if raw_kid in (None, ""):
        return None, None
    try:
        key_id = int(raw_kid)
    except (TypeError, ValueError) as exc:
        raise RedditApiError("账号绑定的 Zernio Key 无效，请重新同步", status_code=400) from exc

    close = False
    session = db
    if session is None:
        from app.core.database import SessionLocal

        session = SessionLocal()
        close = True
    try:
        from app.services.zernio_keys import get_enabled_key

        row = get_enabled_key(session, key_id, site_id=site_id)
        if not row:
            raise RedditApiError("Zernio Key 已删除或禁用，请重新同步", status_code=400)
        return row.api_key, row.profile_id
    finally:
        if close:
            session.close()


def get_reddit_client_for_account(account) -> RedditApiClient:
    cfg = getattr(account, "config", None) if account else None
    db = None
    if account is not None:
        try:
            db = object_session(account)
        except UnmappedInstanceError:
            db = None
    site_id = getattr(account, "site_id", None) if account else None
    api_key, profile_id = resolve_zernio_api_credentials(cfg, db, site_id=site_id)
    return RedditApiClient(
        account_id=getattr(account, "id", 0) or 0,
        zernio_account_id=zernio_account_id_of(account) if account else None,
        access_token=getattr(account, "access_token", None),
        api_key=api_key,
        profile_id=profile_id,
    )
