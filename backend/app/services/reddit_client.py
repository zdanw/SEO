"""Reddit API client, URL parsing, and configuration helpers."""
from __future__ import annotations

import re

_REDDIT_POST_RE = re.compile(
    r"reddit\.com/r/(?P<subreddit>[^/]+)/comments/(?P<post_id>[a-z0-9]+)",
    re.IGNORECASE,
)


def parse_reddit_post_url(url: str) -> tuple[str, str]:
    """Parse a Reddit post URL into (thing_id, subreddit).

    Returns:
        thing_id: e.g. ``t3_abc123``
        subreddit: e.g. ``BabyBumps`` (no ``r/`` prefix)
    """
    m = _REDDIT_POST_RE.search(url.strip())
    if not m:
        raise ValueError(f"Invalid Reddit post URL: {url}")
    return f"t3_{m.group('post_id')}", m.group("subreddit")


def is_reddit_configured() -> bool:
    from app.core.config import settings

    return bool(settings.REDDIT_CLIENT_ID and settings.REDDIT_CLIENT_SECRET)
