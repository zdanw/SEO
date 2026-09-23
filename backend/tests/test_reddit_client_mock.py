import pytest

from app.services.reddit_client import RedditApiClient, parse_reddit_post_url
from app.services.zernio_client import (
    ZernioClient,
    ZernioError,
    is_zernio_configured,
    reset_search_runtime_state,
)


@pytest.fixture(autouse=True)
def _reset_zernio_runtime():
    reset_search_runtime_state()


def test_mock_submit_post():
    client = RedditApiClient(account_id=1, access_token=None)
    result = client.submit_post("BabyBumps", "Test title", "Test body")
    assert result["id"].startswith("t3_")
    assert "permalink" in result


def test_mock_submit_comment():
    client = RedditApiClient(account_id=1, access_token=None)
    result = client.submit_comment("t3_abc123", "Nice post!", "BabyBumps")
    assert result["id"].startswith("t1_")


def test_mock_search_posts():
    client = RedditApiClient(account_id=1, access_token=None)
    items = client.search_posts("BabyBumps", "baby monitor", limit=3)
    assert len(items) == 3
    assert items[0]["thing_id"].startswith("t3_")


def test_zernio_unconfigured_is_mock():
    assert is_zernio_configured() is False
    z = ZernioClient()
    accounts = z.list_reddit_accounts()
    assert accounts[0]["_id"] == "mock_zernio_reddit"


def test_parse_url_still_works():
    thing_id, subreddit = parse_reddit_post_url(
        "https://www.reddit.com/r/BabyBumps/comments/abc123/title/"
    )
    assert thing_id == "t3_abc123"
    assert subreddit == "BabyBumps"


def test_parse_429_retry_from_message():
    msg, retry = ZernioClient._parse_error(
        429,
        '{"error":"Reddit rate limit reached for this account. Retry in 202s.","code":"rate_limited"}',
    )
    assert retry == 202
    assert "rate_limited" in msg


def test_classify_429_quota_vs_rate_limit():
    from app.services.zernio_client import _classify_429

    assert (
        _classify_429(
            "Zernio 429 (rate_limited): Retry in 202s",
            '{"error":"Reddit rate limit reached. Retry in 202s.","code":"rate_limited"}',
        )
        == "rate_limited"
    )
    assert (
        _classify_429(
            "Zernio 429: Quota resets in 3600s",
            '{"error":"Daily quota exceeded. Quota resets in 3600s."}',
        )
        == "quota_exhausted"
    )
    assert _classify_429("Zernio 429: too many requests", "") == "unknown_429"


def test_map_search_items_normalizes_permalink():
    items = ZernioClient._map_search_items(
        [{
            "id": "abc123",
            "fullname": "t3_abc123",
            "title": "hi",
            "permalink": "/r/BabyBumps/comments/abc123/hi/",
            "createdUtc": 1725000000.7,
            "numComments": 3,
            "score": 10,
            "selftext": "body",
            "subreddit": "BabyBumps",
        }],
        "BabyBumps",
    )
    assert items[0]["url"].startswith("https://www.reddit.com/")
    assert items[0]["created_utc"] == 1725000000
    assert items[0]["num_comments"] == 3


def test_get_post_context_ignores_unrelated_hits():
    z = ZernioClient(account_id=1, api_key="sk_test")

    def fake_search(account_id, subreddit, keyword, limit=10):
        return [{
            "title": "Formula brands discussion",
            "url": "https://www.reddit.com/r/Buyingforbaby/comments/zzz/formula/",
            "thing_id": "t3_zzz",
            "subreddit": subreddit,
            "body": "I tried three formula brands",
        }]

    z.search_posts = fake_search  # type: ignore[method-assign]
    ctx = z.get_post_context("acc", "t3_monitor123", "Buyingforbaby")
    assert ctx["title"] == ""
    assert ctx["body"] == ""


def test_get_post_context_returns_exact_match():
    z = ZernioClient(account_id=1, api_key="sk_test")

    def fake_search(account_id, subreddit, keyword, limit=10):
        return [
            {
                "title": "Wrong formula post",
                "url": "https://www.reddit.com/r/Buyingforbaby/comments/zzz/formula/",
                "thing_id": "t3_zzz",
                "subreddit": subreddit,
                "body": "formula",
            },
            {
                "title": "What's the best baby monitor?",
                "url": "https://www.reddit.com/r/Buyingforbaby/comments/monitor123/monitor/",
                "thing_id": "t3_monitor123",
                "subreddit": subreddit,
                "body": "Need recommendations for 2026",
            },
        ]

    z.search_posts = fake_search  # type: ignore[method-assign]
    ctx = z.get_post_context("acc", "t3_monitor123", "Buyingforbaby")
    assert "baby monitor" in ctx["title"].lower()
    assert "recommendations" in ctx["body"].lower()


def test_search_does_not_fallback_on_429():
    z = ZernioClient(account_id=1, api_key="sk_test")
    calls: list[str] = []

    def fake_request(method, path, **kwargs):
        calls.append(path)
        raise ZernioError("limited", status_code=429, retry_after=60)

    z._request = fake_request  # type: ignore[method-assign]
    with pytest.raises(ZernioError) as exc_info:
        z.search_posts("acc", "BabyBumps", "baby motion", 5)
    assert exc_info.value.status_code == 429
    assert calls == ["/reddit/search"]


def test_search_404_account_does_not_fallback():
    z = ZernioClient(account_id=1, api_key="sk_test")
    calls: list[str] = []

    def fake_request(method, path, **kwargs):
        calls.append(path)
        raise ZernioError("Zernio 404: Account not found", status_code=404)

    z._request = fake_request  # type: ignore[method-assign]
    with pytest.raises(ZernioError) as exc_info:
        z.search_posts("acc", "BabyBumps", "baby motion", 5)
    assert exc_info.value.status_code == 404
    assert calls == ["/reddit/search"]


def test_search_404_missing_route_falls_back_to_feed():
    z = ZernioClient(account_id=1, api_key="sk_test")
    calls: list[str] = []

    def fake_request(method, path, **kwargs):
        calls.append(path)
        if path == "/reddit/search":
            raise ZernioError("Zernio 404: empty response", status_code=404)
        return {
            "items": [{
                "id": "abc123",
                "fullname": "t3_abc123",
                "title": "baby motion at night",
                "permalink": "/r/BabyBumps/comments/abc123/hi/",
                "createdUtc": int(__import__("time").time()),
                "selftext": "help",
            }],
        }

    z._request = fake_request  # type: ignore[method-assign]
    items = z.search_posts("acc", "BabyBumps", "baby motion", 5)
    assert calls == ["/reddit/search", "/reddit/feed"]
    assert items[0]["thing_id"] == "t3_abc123"

    calls.clear()
    z.search_posts("acc", "Mommit", "sleep", 5)
    assert calls == ["/reddit/feed"]


def test_list_feed_mock():
    z = ZernioClient()
    items = z.list_feed("acc", "Parenting", limit=2)
    assert len(items) == 2
    assert items[0]["thing_id"].startswith("t3_")
