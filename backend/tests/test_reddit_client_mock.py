from app.services.reddit_client import RedditApiClient


def test_mock_submit_post():
    client = RedditApiClient(account_id=1, access_token=None)
    result = client.submit_post("BabyBumps", "Test title", "Test body")
    assert result["id"].startswith("t3_")
    assert "permalink" in result


def test_mock_submit_comment():
    client = RedditApiClient(account_id=1, access_token=None)
    result = client.submit_comment("t3_abc123", "Nice post!")
    assert result["id"].startswith("t1_")


def test_mock_search_posts():
    client = RedditApiClient(account_id=1, access_token=None)
    items = client.search_posts("BabyBumps", "baby monitor", limit=3)
    assert len(items) == 3
    assert items[0]["thing_id"].startswith("t3_")
