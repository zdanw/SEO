"""Zernio API client for Reddit posting, comments, search, and account connect."""
from __future__ import annotations

import json
import logging
import random
import re
import string
import time
from typing import Any

import httpx

from app.core.config import settings
from app.utils.rate_limiter import CircuitBreaker, acquire_token

logger = logging.getLogger(__name__)

ZERNIO_DEFAULT_BASE = "https://zernio.com/api/v1"
_UNSET = object()
_RETRY_IN_RE = re.compile(r"Retry in (\d+)\s*s", re.IGNORECASE)
_SEARCH_CACHE: dict[tuple[str, str, str, int], tuple[float, list[dict[str, Any]]]] = {}
_SEARCH_CACHE_TTL = 120.0
_SEARCH_CACHE_STALE_TTL = 600.0
_SEARCH_UNSUPPORTED = False
_ACCOUNT_404_HINTS = ("account not found", "account_not_found", "unknown account")


class ZernioError(RuntimeError):
    """Zernio API call failed."""

    def __init__(
        self,
        message: str,
        status_code: int | None = None,
        retry_after: int | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.retry_after = retry_after


def is_zernio_configured() -> bool:
    """Deprecated: credentials live in DB only. Always False without an injected api_key."""
    return False


def _mock_id(prefix: str, n: int = 8) -> str:
    return prefix + "".join(random.choices(string.ascii_lowercase + string.digits, k=n))


class ZernioClient:
    def __init__(
        self,
        account_id: int = 0,
        api_key: Any = _UNSET,
        profile_id: Any = _UNSET,
    ) -> None:
        self.account_id = account_id
        self.api_key = None if api_key is _UNSET else api_key
        self.profile_id = None if profile_id is _UNSET else profile_id
        self.api_base = (settings.ZERNIO_API_BASE or ZERNIO_DEFAULT_BASE).rstrip("/")
        self.timeout = settings.ZERNIO_TIMEOUT
        self._mock_mode = not bool(self.api_key)

    def _headers(self, *, json_body: bool) -> dict[str, str]:
        headers = {"Authorization": f"Bearer {self.api_key}"}
        if json_body:
            headers["Content-Type"] = "application/json"
        return headers

    @staticmethod
    def _unwrap(data: Any) -> dict[str, Any]:
        if not isinstance(data, dict):
            return {"items": data} if isinstance(data, list) else {"data": data}
        inner = data.get("data")
        if isinstance(inner, dict) and "items" in inner and "items" not in data:
            return inner
        return data

    @staticmethod
    def _parse_error(status_code: int, text: str) -> tuple[str, int | None]:
        snippet = (text or "").strip()
        try:
            obj = json.loads(snippet) if snippet else {}
        except json.JSONDecodeError:
            obj = {}
        err = None
        code = None
        retry_after = None
        if isinstance(obj, dict):
            err = obj.get("error") or obj.get("message") or obj.get("detail")
            code = obj.get("code") or obj.get("type")
            for key in ("retryAfter", "retry_after", "retryIn", "retry_in"):
                raw = obj.get(key)
                if isinstance(raw, (int, float)):
                    retry_after = int(raw)
                    break
                if isinstance(raw, str) and raw.isdigit():
                    retry_after = int(raw)
                    break
        blob = err if isinstance(err, str) else snippet
        if retry_after is None:
            matched = _RETRY_IN_RE.search(blob or "")
            if matched:
                retry_after = int(matched.group(1))
        if err and code:
            msg = f"Zernio {status_code} ({code}): {err}"
        elif err:
            msg = f"Zernio {status_code}: {err}"
        else:
            msg = f"Zernio {status_code}: {snippet[:200] or 'empty response'}"
        return msg, retry_after

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict | None = None,
        json_body: dict | None = None,
    ) -> dict[str, Any]:
        is_write = method.upper() not in ("GET", "HEAD")
        breaker = None
        if is_write:
            limiter_key = self.account_id or 0
            if not acquire_token("reddit", limiter_key):
                raise ZernioError("发布过于频繁，请稍后再试", status_code=429, retry_after=10)
            breaker = CircuitBreaker("reddit", limiter_key)
            if not breaker.allow_request():
                raise ZernioError("Reddit 发布通道暂时熔断，请稍后再试", status_code=503)
        url = f"{self.api_base}{path}"
        try:
            req_kwargs: dict[str, Any] = {
                "headers": self._headers(json_body=json_body is not None),
                "params": params,
            }
            if json_body is not None:
                req_kwargs["json"] = json_body
            with httpx.Client(timeout=httpx.Timeout(float(self.timeout))) as client:
                resp = client.request(method, url, **req_kwargs)
            if resp.status_code >= 400:
                msg, retry_after = self._parse_error(resp.status_code, resp.text)
                logger.warning("Zernio %s %s -> %s", method, path, resp.status_code)
                if breaker is not None and (resp.status_code >= 500 or resp.status_code == 429):
                    breaker.record_failure()
                raise ZernioError(msg, status_code=resp.status_code, retry_after=retry_after)
            if breaker is not None:
                breaker.record_success()
            if not resp.content:
                return {}
            return self._unwrap(resp.json())
        except ZernioError:
            raise
        except httpx.TimeoutException as exc:
            if breaker is not None:
                breaker.record_failure()
            raise ZernioError(
                f"Zernio 请求超时（{self.timeout}s），请稍后重试",
                status_code=504,
            ) from exc
        except Exception as exc:
            if breaker is not None:
                breaker.record_failure()
            raise ZernioError(str(exc)) from exc

    def ensure_profile_id(self) -> str:
        if self.profile_id:
            return self.profile_id
        if self._mock_mode:
            return "mock_profile"
        data = self._request("GET", "/profiles")
        profiles = data.get("profiles") or data.get("data") or []
        if isinstance(profiles, dict):
            profiles = profiles.get("profiles") or []
        if profiles:
            first = profiles[0]
            return str(first.get("_id") or first.get("id") or "")
        created = self._request(
            "POST",
            "/profiles",
            json_body={"name": "SEO Platform", "description": "Reddit operations via Zernio"},
        )
        profile = created.get("profile") or created
        pid = str(profile.get("_id") or profile.get("id") or "")
        if not pid:
            raise ZernioError("Zernio 未返回 profile id")
        return pid

    def get_connect_url(self, redirect_url: str) -> str:
        if self._mock_mode:
            return f"{redirect_url}?connected=reddit&accountId=mock_acc&username=mock_user"
        profile_id = self.ensure_profile_id()
        data = self._request(
            "GET",
            "/connect/reddit",
            params={"profileId": profile_id, "redirect_url": redirect_url},
        )
        url = data.get("authUrl") or data.get("auth_url") or data.get("url")
        if not url:
            raise ZernioError("Zernio 未返回 authUrl")
        return str(url)

    def list_reddit_accounts(self) -> list[dict[str, Any]]:
        if self._mock_mode:
            return [
                {
                    "_id": "mock_zernio_reddit",
                    "platform": "reddit",
                    "username": "u/mock_user",
                    "displayName": "mock_user",
                    "isActive": True,
                }
            ]
        params: dict[str, str] = {"platform": "reddit"}
        if self.profile_id:
            params["profileId"] = self.profile_id
        data = self._request("GET", "/accounts", params=params)
        accounts = data.get("accounts") or []
        return [a for a in accounts if str(a.get("platform", "")).lower() == "reddit"]

    def submit_post(self, zernio_account_id: str, subreddit: str, title: str, body: str) -> dict[str, Any]:
        if self._mock_mode:
            post_id = _mock_id("t3_mock_")
            return {
                "id": post_id,
                "name": post_id,
                "permalink": f"https://www.reddit.com/r/{subreddit}/comments/{post_id[3:]}/mock_post/",
                "mock": True,
            }
        data = self._request(
            "POST",
            "/posts",
            json_body={
                "content": body,
                "title": title,
                "publishNow": True,
                "platforms": [
                    {
                        "platform": "reddit",
                        "accountId": zernio_account_id,
                        "platformSpecificData": {"subreddit": subreddit, "title": title},
                    }
                ],
            },
        )
        post = data.get("post") or data
        platforms = post.get("platforms") or data.get("platforms") or []
        first = platforms[0] if platforms else {}
        permalink = (
            first.get("platformPostUrl")
            or first.get("url")
            or post.get("platformPostUrl")
            or ""
        )
        platform_id = first.get("platformPostId") or first.get("id") or post.get("_id") or ""
        return {
            "id": str(platform_id),
            "name": str(platform_id),
            "permalink": permalink,
            "zernio_post_id": str(post.get("_id") or ""),
        }

    def submit_comment(
        self,
        zernio_account_id: str,
        thing_id: str,
        body: str,
        subreddit: str | None = None,
    ) -> dict[str, Any]:
        if self._mock_mode:
            cid = _mock_id("t1_mock_")
            return {"id": cid, "name": cid, "mock": True}
        payload: dict[str, Any] = {"accountId": zernio_account_id, "message": body}
        if subreddit:
            payload["subreddit"] = subreddit
        data = self._request("POST", f"/inbox/comments/{thing_id}", json_body=payload)
        comment = data.get("comment") or data
        cid = comment.get("id") or comment.get("_id") or comment.get("name") or ""
        return {"id": str(cid), "name": str(cid)}

    def get_post_context(self, zernio_account_id: str, thing_id: str, subreddit: str) -> dict[str, Any]:
        if self._mock_mode:
            return {
                "title": "Mock post title about baby monitors",
                "body": "Looking for recommendations on baby monitors. What do you use?",
                "subreddit": subreddit or "BabyBumps",
            }
        post_id = thing_id.removeprefix("t3_")

        def _pick_exact(items: list[dict[str, Any]]) -> dict[str, Any] | None:
            for item in items:
                tid = str(item.get("thing_id") or "")
                if tid == thing_id or tid.endswith(post_id):
                    return {
                        "title": item.get("title") or "",
                        "body": item.get("body") or "",
                        "subreddit": item.get("subreddit") or subreddit,
                    }
            return None

        # Search by Reddit post id, then by url:id. Never return an unrelated hit.
        for query in (post_id, f"url:{post_id}"):
            try:
                found = _pick_exact(self.search_posts(zernio_account_id, subreddit, query, limit=10))
            except ZernioError:
                found = None
            if found:
                return found
        return {"title": "", "body": "", "subreddit": subreddit}

    def list_feed(
        self,
        zernio_account_id: str,
        subreddit: str,
        limit: int = 10,
        sort: str = "new",
    ) -> list[dict[str, Any]]:
        limit = min(max(limit, 1), 25)
        if self._mock_mode:
            return self._mock_search_items(subreddit, "feed", limit)
        data = self._request(
            "GET",
            "/reddit/feed",
            params={
                "accountId": zernio_account_id,
                "subreddit": subreddit,
                "sort": sort,
                "limit": limit,
            },
        )
        return self._map_search_items(data.get("items") or [], subreddit)

    def vote_reddit_thing(
        self,
        zernio_account_id: str,
        thing_id: str,
        direction: int = 1,
    ) -> dict[str, Any]:
        """Upvote/downvote/clear via POST /accounts/{id}/reddit-vote."""
        tid = (thing_id or "").strip()
        if not tid:
            raise ZernioError("thingId 不能为空", status_code=400)
        if direction not in (1, 0, -1):
            raise ZernioError("direction 必须是 1 / 0 / -1", status_code=400)
        if self._mock_mode:
            return {"ok": True, "thingId": tid, "direction": direction, "mock": True}
        data = self._request(
            "POST",
            f"/accounts/{zernio_account_id}/reddit-vote",
            json_body={"thingId": tid, "direction": direction},
        )
        return data if isinstance(data, dict) else {"ok": True, "thingId": tid, "direction": direction}

    def list_post_comments(
        self,
        zernio_account_id: str,
        thing_id: str,
        subreddit: str,
        limit: int = 8,
    ) -> list[dict[str, Any]]:
        """List comments on a Reddit post via GET /inbox/comments/{postId}."""
        limit = min(max(limit, 1), 15)
        post_id = (thing_id or "").strip().removeprefix("t3_")
        if not post_id:
            raise ZernioError("帖子 ID 不能为空", status_code=400)
        if self._mock_mode:
            return [
                {
                    "thing_id": f"t1_mock_{i}",
                    "body": f"Mock comment {i} about the post",
                    "author": f"user{i}",
                    "score": 3 - i,
                    "created_utc": int(time.time()) - i * 600,
                    "url": "",
                }
                for i in range(1, min(limit, 4) + 1)
            ]
        sr = (subreddit or "").strip().removeprefix("r/").removeprefix("/")
        params: dict[str, Any] = {"accountId": zernio_account_id, "limit": limit}
        if sr:
            params["subreddit"] = sr
        data = self._request(
            "GET",
            f"/inbox/comments/{post_id}",
            params=params,
        )
        raw = data.get("comments") if isinstance(data, dict) else None
        if not isinstance(raw, list):
            raw = data.get("items") if isinstance(data, dict) else []
        return self._map_comment_items(raw or [])[:limit]

    @staticmethod
    def _map_comment_items(raw_items: list) -> list[dict[str, Any]]:
        mapped: list[dict[str, Any]] = []

        def _one(c: dict[str, Any]) -> dict[str, Any] | None:
            cid = str(c.get("id") or c.get("cid") or c.get("name") or "").strip()
            if not cid:
                return None
            if cid.startswith("t1_") or cid.startswith("t3_"):
                thing_id = cid
            else:
                thing_id = f"t1_{cid}"
            body = str(c.get("message") or c.get("body") or c.get("text") or "")
            author = ""
            frm = c.get("from")
            if isinstance(frm, dict):
                author = str(frm.get("username") or frm.get("name") or "")
            else:
                author = str(c.get("author") or "")
            return {
                "thing_id": thing_id,
                "body": body,
                "author": author,
                "score": _as_int(c.get("likeCount") if c.get("likeCount") is not None else c.get("score")),
                "created_utc": _as_int(c.get("createdUtc") if c.get("createdUtc") is not None else c.get("created_utc")),
                "url": str(c.get("url") or ""),
            }

        for item in raw_items:
            if not isinstance(item, dict):
                continue
            top = _one(item)
            if top:
                mapped.append(top)
            replies = item.get("replies")
            if isinstance(replies, list):
                for reply in replies:
                    if isinstance(reply, dict):
                        nested = _one(reply)
                        if nested:
                            mapped.append(nested)
        return mapped

    def search_posts(
        self,
        zernio_account_id: str,
        subreddit: str,
        keyword: str,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        limit = min(max(limit, 1), 25)
        if self._mock_mode:
            return self._mock_search_items(subreddit, keyword, limit)
        cache_key = (zernio_account_id, subreddit.lower(), keyword.lower().strip(), limit)
        cached = _cache_get(cache_key, _SEARCH_CACHE_TTL)
        if cached is not None:
            return cached
        if _SEARCH_UNSUPPORTED:
            items = self._filter_feed(self.list_feed(zernio_account_id, subreddit, limit=limit), keyword)
            _cache_set(cache_key, items)
            return items
        try:
            data = self._request(
                "GET",
                "/reddit/search",
                params={
                    "accountId": zernio_account_id,
                    "q": keyword,
                    "subreddit": subreddit,
                    "restrict_sr": "1",
                    "sort": "new",
                    "limit": limit,
                },
            )
            items = self._map_search_items(data.get("items") or [], subreddit)
            _cache_set(cache_key, items)
            return items
        except ZernioError as exc:
            stale = _cache_get(cache_key, _SEARCH_CACHE_STALE_TTL)
            if stale is not None:
                logger.warning("Zernio search unavailable (%s), serving cached results", exc.status_code)
                return stale
            if not _is_missing_search_route(exc):
                raise
            _mark_search_unsupported()
            logger.warning("Zernio /reddit/search unavailable (%s), using subreddit feed", exc)
            items = self._filter_feed(self.list_feed(zernio_account_id, subreddit, limit=limit), keyword)
            _cache_set(cache_key, items)
            return items

    @staticmethod
    def _mock_search_items(subreddit: str, keyword: str, limit: int) -> list[dict[str, Any]]:
        items = []
        for i in range(limit):
            pid = "".join(random.choices(string.ascii_lowercase + string.digits, k=6))
            items.append({
                "title": f"Mock: {keyword} discussion #{i + 1}",
                "url": f"https://www.reddit.com/r/{subreddit}/comments/{pid}/mock_discussion/",
                "thing_id": f"t3_{pid}",
                "subreddit": subreddit,
                "score": random.randint(1, 200),
                "num_comments": random.randint(0, 50),
                "created_utc": int(time.time()) - i * 600,
                "body": "",
            })
        return items

    @staticmethod
    def _filter_feed(mapped: list[dict[str, Any]], keyword: str) -> list[dict[str, Any]]:
        kw = (keyword or "").strip().lower()
        if not kw:
            return mapped
        filtered = [
            item
            for item in mapped
            if kw in f"{item.get('title', '')} {item.get('body', '')}".lower()
        ]
        return filtered or mapped

    @staticmethod
    def _map_search_items(raw_items: list, subreddit: str) -> list[dict[str, Any]]:
        mapped: list[dict[str, Any]] = []
        for post in raw_items:
            if not isinstance(post, dict):
                continue
            fullname = str(post.get("fullname") or "")
            short_id = str(post.get("id") or "").removeprefix("t3_")
            thing_id = fullname if fullname.startswith("t3_") else (f"t3_{short_id}" if short_id else "")
            permalink = str(post.get("permalink") or post.get("url") or "")
            if permalink.startswith("/"):
                permalink = f"https://www.reddit.com{permalink}"
            if not permalink and short_id:
                permalink = f"https://www.reddit.com/r/{subreddit}/comments/{short_id}/"
            mapped.append({
                "title": post.get("title") or "",
                "url": permalink,
                "thing_id": thing_id,
                "subreddit": post.get("subreddit") or subreddit,
                "score": _as_int(post.get("score")),
                "num_comments": _as_int(post.get("numComments") or post.get("num_comments")),
                "created_utc": _as_int(post.get("createdUtc") if post.get("createdUtc") is not None else post.get("created_utc")),
                "body": post.get("selftext") or "",
            })
        return [item for item in mapped if item["thing_id"] and item["url"]]


def reset_search_runtime_state() -> None:
    global _SEARCH_UNSUPPORTED
    _SEARCH_UNSUPPORTED = False
    _SEARCH_CACHE.clear()


def _mark_search_unsupported() -> None:
    global _SEARCH_UNSUPPORTED
    _SEARCH_UNSUPPORTED = True


def _is_missing_search_route(exc: ZernioError) -> bool:
    if exc.status_code == 405:
        return True
    if exc.status_code != 404:
        return False
    msg = str(exc).lower()
    if any(hint in msg for hint in _ACCOUNT_404_HINTS) or "account" in msg:
        return False
    return True


def _as_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or value == "":
            return default
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _cache_get(key: tuple[str, str, str, int], max_age: float) -> list[dict[str, Any]] | None:
    hit = _SEARCH_CACHE.get(key)
    if not hit:
        return None
    stored_at, items = hit
    if time.monotonic() - stored_at <= max_age:
        return items
    return None


def _cache_set(key: tuple[str, str, str, int], items: list[dict[str, Any]]) -> None:
    if len(_SEARCH_CACHE) >= 32:
        oldest = min(_SEARCH_CACHE, key=lambda k: _SEARCH_CACHE[k][0])
        _SEARCH_CACHE.pop(oldest, None)
    _SEARCH_CACHE[key] = (time.monotonic(), items)


def get_zernio_client(
    account_id: int = 0,
    api_key: Any = _UNSET,
    profile_id: Any = _UNSET,
) -> ZernioClient:
    kwargs: dict[str, Any] = {}
    if api_key is not _UNSET:
        kwargs["api_key"] = api_key
    if profile_id is not _UNSET:
        kwargs["profile_id"] = profile_id
    return ZernioClient(account_id=account_id, **kwargs)
