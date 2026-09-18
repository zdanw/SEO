"""Social channel protocol. Reddit is the only implementation this round."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from app.services.reddit_client import (
    RedditApiError,
    get_reddit_client_for_account,
    is_reddit_configured,
)
from app.services import reddit_oauth

PUBLISHABLE_STATUSES = frozenset({"approved", "failed"})


def can_publish(status: str) -> bool:
    """Approve-required publish; failed records may retry without re-approve."""
    return status in PUBLISHABLE_STATUSES


@dataclass
class PublishResult:
    success: bool
    platform_id: str | None = None
    permalink: str | None = None
    error: str | None = None


@dataclass
class SearchItem:
    title: str
    url: str
    thing_id: str
    subreddit: str
    score: int = 0
    num_comments: int = 0
    created_utc: int = 0


class SocialChannel(Protocol):
    platform_name: str

    def is_configured(self) -> bool: ...

    def get_authorization_url(self, user_id: int, site_id: int) -> str: ...

    def publish_post(self, account: Any, title: str, body: str, **kwargs: Any) -> PublishResult: ...

    def publish_comment(self, account: Any, thing_id: str, body: str, **kwargs: Any) -> PublishResult: ...

    def search(
        self,
        account: Any,
        *,
        subreddit: str,
        keyword: str,
        limit: int = 10,
    ) -> list[SearchItem]: ...


class RedditChannel:
    platform_name = "reddit"

    def is_configured(self) -> bool:
        return is_reddit_configured()

    def get_authorization_url(self, user_id: int, site_id: int) -> str:
        return reddit_oauth.get_authorization_url(user_id, site_id)

    def publish_post(self, account: Any, title: str, body: str, **kwargs: Any) -> PublishResult:
        subreddit = str(kwargs.get("subreddit") or "")
        try:
            client = get_reddit_client_for_account(account)
            result = client.submit_post(subreddit, title, body)
        except RedditApiError as exc:
            return PublishResult(success=False, error=str(exc))
        permalink = result.get("permalink") or ""
        if permalink and not str(permalink).startswith("http"):
            permalink = f"https://www.reddit.com{permalink}"
        return PublishResult(
            success=True,
            platform_id=result.get("name") or result.get("id"),
            permalink=permalink or None,
        )

    def publish_comment(self, account: Any, thing_id: str, body: str, **kwargs: Any) -> PublishResult:
        try:
            client = get_reddit_client_for_account(account)
            result = client.submit_comment(thing_id, body, kwargs.get("subreddit"))
        except RedditApiError as exc:
            return PublishResult(success=False, error=str(exc))
        return PublishResult(
            success=True,
            platform_id=result.get("name") or result.get("id"),
        )

    def search(
        self,
        account: Any,
        *,
        subreddit: str,
        keyword: str,
        limit: int = 10,
    ) -> list[SearchItem]:
        client = get_reddit_client_for_account(account)
        raw = client.search_posts(subreddit, keyword, limit=limit)
        return [
            SearchItem(
                title=item.get("title", ""),
                url=item.get("url", ""),
                thing_id=item.get("thing_id", ""),
                subreddit=item.get("subreddit", subreddit),
                score=int(item.get("score", 0)),
                num_comments=int(item.get("num_comments", 0)),
                created_utc=int(item.get("created_utc", 0)),
            )
            for item in raw
        ]
