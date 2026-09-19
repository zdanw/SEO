"""社区存在性 + 活跃度硬门槛校验。"""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app.services.reddit_community_verify import (
    MIN_POSTS_7D,
    MIN_SUBSCRIBERS,
    VERIFY_CACHE_HOURS,
    apply_verify_to_community,
    is_verify_fresh,
    verify_subreddit,
)


def _about_payload(*, subscribers=50000, accounts_active=120, display_name="Parenting"):
    return {
        "data": {
            "display_name": display_name,
            "subscribers": subscribers,
            "accounts_active": accounts_active,
            "subreddit_type": "public",
        }
    }


def _new_payload(created_offsets_hours: list[float]):
    now = datetime(2026, 9, 19, 12, 0, tzinfo=timezone.utc).timestamp()
    children = []
    for i, hours_ago in enumerate(created_offsets_hours):
        children.append(
            {
                "data": {
                    "id": f"post{i}",
                    "created_utc": now - hours_ago * 3600,
                }
            }
        )
    return {"data": {"children": children}}


def test_verify_existing_active_subreddit():
    calls: list[str] = []

    def http_get(url: str):
        calls.append(url)
        if url.endswith("/about.json"):
            return 200, _about_payload(subscribers=80000, accounts_active=200)
        if "/new.json" in url:
            # 5 posts in last 7 days
            return 200, _new_payload([1, 12, 36, 72, 100])
        raise AssertionError(url)

    result = verify_subreddit(
        "Parenting",
        http_get=http_get,
        now=datetime(2026, 9, 19, 12, 0, tzinfo=timezone.utc),
    )
    assert result.exists is True
    assert result.subscribers == 80000
    assert result.accounts_active == 200
    assert result.posts_7d == 5
    assert result.is_active_enough is True
    assert result.error is None
    assert any(u.endswith("/about.json") for u in calls)
    assert any("/new.json" in u for u in calls)


def test_verify_blocked_does_not_claim_missing():
    """Reddit 封锁/403 时应标记为无法验证，不能当成假社区滤掉。"""

    def http_get(url: str):
        return 403, {"raw": "<title>Blocked</title>"}

    result = verify_subreddit("Parenting", http_get=http_get)
    assert result.exists is None
    assert result.is_active_enough is True
    assert "403" in (result.error or "")


def test_verify_missing_subreddit():
    def http_get(url: str):
        if url.endswith("/about.json"):
            return 404, {"message": "Not Found", "error": 404}
        raise AssertionError("should not fetch new.json when missing")

    result = verify_subreddit("ThisSubDoesNotExist999", http_get=http_get)
    assert result.exists is False
    assert result.is_active_enough is False
    assert result.error


def test_verify_low_activity_marks_not_enough():
    def http_get(url: str):
        if url.endswith("/about.json"):
            return 200, _about_payload(subscribers=MIN_SUBSCRIBERS - 1, accounts_active=2)
        if "/new.json" in url:
            return 200, _new_payload([1])  # only 1 post in 7d
        raise AssertionError(url)

    result = verify_subreddit(
        "TinySub",
        http_get=http_get,
        now=datetime(2026, 9, 19, 12, 0, tzinfo=timezone.utc),
    )
    assert result.exists is True
    assert result.posts_7d == 1
    assert result.is_active_enough is False
    assert result.subscribers == MIN_SUBSCRIBERS - 1


def test_verify_counts_only_posts_within_7_days():
    def http_get(url: str):
        if url.endswith("/about.json"):
            return 200, _about_payload()
        if "/new.json" in url:
            # 2 recent + 2 older than 7d
            return 200, _new_payload([24, 48, 200, 300])
        raise AssertionError(url)

    result = verify_subreddit(
        "Parenting",
        http_get=http_get,
        now=datetime(2026, 9, 19, 12, 0, tzinfo=timezone.utc),
    )
    assert result.posts_7d == 2
    assert result.is_active_enough is (2 >= MIN_POSTS_7D and 50000 >= MIN_SUBSCRIBERS)


def test_apply_verify_sets_inactive_when_dead():
    row = SimpleNamespace(
        name="TinySub",
        is_active=True,
        verified_at=None,
        exists=None,
        subscribers=None,
        accounts_active=None,
        posts_7d=None,
        activity_score=None,
        verify_error=None,
    )
    result = verify_subreddit(
        "TinySub",
        http_get=lambda url: (
            (200, _about_payload(subscribers=10))
            if url.endswith("/about.json")
            else (200, _new_payload([]))
        ),
        now=datetime(2026, 9, 19, 12, 0, tzinfo=timezone.utc),
    )
    apply_verify_to_community(row, result)
    assert row.exists is True
    assert row.is_active is False
    assert row.subscribers == 10
    assert row.posts_7d == 0
    assert row.verified_at is not None


def test_is_verify_fresh_respects_cache_window():
    now = datetime(2026, 9, 19, 12, 0, tzinfo=timezone.utc)
    fresh = SimpleNamespace(verified_at=now - timedelta(hours=VERIFY_CACHE_HOURS - 1))
    stale = SimpleNamespace(verified_at=now - timedelta(hours=VERIFY_CACHE_HOURS + 1))
    missing = SimpleNamespace(verified_at=None)
    assert is_verify_fresh(fresh, now=now) is True
    assert is_verify_fresh(stale, now=now) is False
    assert is_verify_fresh(missing, now=now) is False
