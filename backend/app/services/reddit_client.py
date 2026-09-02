"""Reddit API client, URL parsing, and configuration helpers."""
from __future__ import annotations

import logging
import random
import re
import string
from typing import Any
from urllib.parse import urlencode

import httpx

from app.core.config import settings
from app.utils.rate_limiter import CircuitBreaker, acquire_token

logger = logging.getLogger(__name__)

_REDDIT_POST_RE = re.compile(
    r"reddit\.com/r/(?P<subreddit>[^/]+)/comments/(?P<post_id>[a-z0-9]+)",
    re.IGNORECASE,
)

REDDIT_OAUTH_BASE = "https://www.reddit.com"
REDDIT_API_BASE = "https://oauth.reddit.com"


class RedditApiError(RuntimeError):
    """Reddit API call failed."""


def parse_reddit_post_url(url: str) -> tuple[str, str]:
    """Parse a Reddit post URL into (thing_id, subreddit)."""
    m = _REDDIT_POST_RE.search(url.strip())
    if not m:
        raise ValueError(f"Invalid Reddit post URL: {url}")
    return f"t3_{m.group('post_id')}", m.group("subreddit")


def is_reddit_configured() -> bool:
    return bool(settings.REDDIT_CLIENT_ID and settings.REDDIT_CLIENT_SECRET)


def normalize_subreddit(name: str) -> str:
    return name.strip().removeprefix("r/").removeprefix("/")


class RedditApiClient:
    """Reddit OAuth API wrapper with mock fallback for local dev."""

    def __init__(
        self,
        account_id: int,
        access_token: str | None,
        refresh_token: str | None = None,
    ) -> None:
        self.account_id = account_id
        self.access_token = access_token
        self.refresh_token = refresh_token
        self._mock_mode = not is_reddit_configured() or not access_token

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"bearer {self.access_token}",
            "User-Agent": settings.REDDIT_USER_AGENT,
        }

    def _request(
        self,
        method: str,
        url: str,
        *,
        params: dict | None = None,
        data: dict | None = None,
    ) -> dict[str, Any]:
        if not acquire_token("reddit", self.account_id):
            raise RedditApiError(f"Reddit account {self.account_id} rate limited")
        breaker = CircuitBreaker("reddit", self.account_id)
        if not breaker.allow_request():
            raise RedditApiError(f"Reddit account {self.account_id} circuit open")
        try:
            with httpx.Client(timeout=30) as client:
                resp = client.request(
                    method,
                    url,
                    headers=self._headers(),
                    params=params,
                    data=data,
                )
            if resp.status_code >= 400:
                raise RedditApiError(f"Reddit API {resp.status_code}: {resp.text[:300]}")
            breaker.record_success()
            return resp.json()
        except RedditApiError:
            breaker.record_failure()
            raise
        except Exception as exc:
            breaker.record_failure()
            raise RedditApiError(str(exc)) from exc

    def get_me(self) -> dict[str, Any]:
        if self._mock_mode:
            return {"name": f"mock_user_{self.account_id}"}
        data = self._request("GET", f"{REDDIT_API_BASE}/api/v1/me")
        return data

    def submit_post(self, subreddit: str, title: str, body: str) -> dict[str, Any]:
        sr = normalize_subreddit(subreddit)
        if self._mock_mode:
            post_id = "t3_mock_" + "".join(random.choices(string.ascii_lowercase + string.digits, k=8))
            return {
                "id": post_id,
                "name": post_id,
                "permalink": f"/r/{sr}/comments/{post_id[3:]}/mock_post/",
                "mock": True,
            }
        data = self._request(
            "POST",
            f"{REDDIT_API_BASE}/api/submit",
            data={"kind": "self", "sr": sr, "title": title, "text": body, "api_type": "json"},
        )
        json_data = data.get("json", {}).get("data", {})
        if data.get("json", {}).get("errors"):
            raise RedditApiError(str(data["json"]["errors"]))
        return {
            "id": json_data.get("name") or json_data.get("id"),
            "name": json_data.get("name"),
            "permalink": json_data.get("url") or json_data.get("permalink", ""),
        }

    def submit_comment(self, thing_id: str, body: str) -> dict[str, Any]:
        if self._mock_mode:
            cid = "t1_mock_" + "".join(random.choices(string.ascii_lowercase + string.digits, k=8))
            return {"id": cid, "name": cid, "mock": True}
        data = self._request(
            "POST",
            f"{REDDIT_API_BASE}/api/comment",
            data={"thing_id": thing_id, "text": body, "api_type": "json"},
        )
        json_data = data.get("json", {}).get("data", {}).get("things", [{}])
        if data.get("json", {}).get("errors"):
            raise RedditApiError(str(data["json"]["errors"]))
        thing = json_data[0].get("data", {}) if json_data else {}
        return {"id": thing.get("name"), "name": thing.get("name")}

    def get_post_context(self, thing_id: str) -> dict[str, Any]:
        post_id = thing_id.removeprefix("t3_")
        if self._mock_mode:
            return {
                "title": "Mock post title about baby monitors",
                "body": "Looking for recommendations on baby monitors. What do you use?",
                "subreddit": "BabyBumps",
            }
        data = self._request(
            "GET",
            f"{REDDIT_API_BASE}/comments/{post_id}.json",
            params={"limit": 1},
        )
        if not data or not isinstance(data, list):
            raise RedditApiError("Unexpected Reddit comments response")
        post_listing = data[0]["data"]["children"][0]["data"]
        return {
            "title": post_listing.get("title", ""),
            "body": post_listing.get("selftext", "") or post_listing.get("body", ""),
            "subreddit": post_listing.get("subreddit", ""),
        }

    def search_posts(self, subreddit: str, keyword: str, limit: int = 10) -> list[dict[str, Any]]:
        sr = normalize_subreddit(subreddit)
        limit = min(max(limit, 1), 25)
        if self._mock_mode:
            items = []
            for i in range(limit):
                pid = "".join(random.choices(string.ascii_lowercase + string.digits, k=6))
                items.append({
                    "title": f"Mock: {keyword} discussion #{i + 1}",
                    "url": f"https://www.reddit.com/r/{sr}/comments/{pid}/mock_discussion/",
                    "thing_id": f"t3_{pid}",
                    "subreddit": sr,
                    "score": random.randint(1, 200),
                    "num_comments": random.randint(0, 50),
                    "created_utc": 1725000000 + i,
                })
            return items
        data = self._request(
            "GET",
            f"{REDDIT_API_BASE}/r/{sr}/search",
            params={
                "q": keyword,
                "restrict_sr": "1",
                "sort": "relevance",
                "limit": str(limit),
                "type": "link",
            },
        )
        items: list[dict[str, Any]] = []
        for child in data.get("data", {}).get("children", []):
            post = child.get("data", {})
            permalink = post.get("permalink", "")
            url = f"https://www.reddit.com{permalink}" if permalink else post.get("url", "")
            items.append({
                "title": post.get("title", ""),
                "url": url,
                "thing_id": post.get("name", ""),
                "subreddit": post.get("subreddit", sr),
                "score": int(post.get("score", 0)),
                "num_comments": int(post.get("num_comments", 0)),
                "created_utc": int(post.get("created_utc", 0)),
            })
        return items

    def refresh_access_token(self) -> dict[str, Any]:
        if not is_reddit_configured() or not self.refresh_token:
            raise RedditApiError("Cannot refresh token: missing credentials")
        auth = (settings.REDDIT_CLIENT_ID, settings.REDDIT_CLIENT_SECRET)
        with httpx.Client(timeout=30) as client:
            resp = client.post(
                f"{REDDIT_OAUTH_BASE}/api/v1/access_token",
                auth=auth,
                data={"grant_type": "refresh_token", "refresh_token": self.refresh_token},
                headers={"User-Agent": settings.REDDIT_USER_AGENT},
            )
        if resp.status_code >= 400:
            raise RedditApiError(f"Token refresh failed: {resp.text[:200]}")
        return resp.json()


def get_reddit_client_for_account(account) -> RedditApiClient:
    """Build RedditApiClient from a SocialAccount ORM row."""
    return RedditApiClient(
        account_id=account.id,
        access_token=account.access_token,
        refresh_token=account.refresh_token,
    )


def build_reddit_auth_url(state: str) -> str:
    params = {
        "client_id": settings.REDDIT_CLIENT_ID,
        "response_type": "code",
        "state": state,
        "redirect_uri": settings.REDDIT_REDIRECT_URI,
        "duration": "permanent",
        "scope": "identity read submit",
    }
    return f"{REDDIT_OAUTH_BASE}/api/v1/authorize?{urlencode(params)}"


def exchange_reddit_code(code: str) -> dict[str, Any]:
    auth = (settings.REDDIT_CLIENT_ID, settings.REDDIT_CLIENT_SECRET)
    with httpx.Client(timeout=30) as client:
        resp = client.post(
            f"{REDDIT_OAUTH_BASE}/api/v1/access_token",
            auth=auth,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": settings.REDDIT_REDIRECT_URI,
            },
            headers={"User-Agent": settings.REDDIT_USER_AGENT},
        )
    if resp.status_code >= 400:
        raise RedditApiError(f"Token exchange failed: {resp.text[:200]}")
    return resp.json()
