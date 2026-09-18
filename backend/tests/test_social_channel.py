from types import SimpleNamespace

from app.services.social_channel import RedditChannel, can_publish
from app.services.zernio_client import _SEARCH_CACHE


def setup_function():
    _SEARCH_CACHE.clear()


def test_only_approved_and_failed_can_publish():
    assert can_publish("approved") is True
    assert can_publish("failed") is True
    assert can_publish("pending_review") is False
    assert can_publish("posted") is False
    assert can_publish("rejected") is False


def test_reddit_channel_mock_publish_post():
    channel = RedditChannel()
    account = SimpleNamespace(id=1, access_token=None, refresh_token=None)
    result = channel.publish_post(account, title="Hello", body="Body", subreddit="BabyBumps")
    assert result.success is True
    assert result.platform_id
    assert result.platform_id.startswith("t3_")
    assert result.permalink


def test_reddit_channel_mock_publish_comment():
    channel = RedditChannel()
    account = SimpleNamespace(id=1, access_token=None, refresh_token=None)
    result = channel.publish_comment(account, thing_id="t3_abc123", body="Nice post")
    assert result.success is True
    assert result.platform_id
    assert result.platform_id.startswith("t1_")


def test_reddit_channel_mock_search():
    channel = RedditChannel()
    account = SimpleNamespace(id=1, access_token=None, refresh_token=None)
    items = channel.search(account, subreddit="BabyBumps", keyword="monitor", limit=2)
    assert len(items) == 2
    assert items[0].thing_id.startswith("t3_")
    assert items[0].subreddit == "BabyBumps"
