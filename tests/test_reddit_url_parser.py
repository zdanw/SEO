import pytest

from app.services.reddit_client import parse_reddit_post_url


def test_parse_standard_reddit_url():
    url = "https://www.reddit.com/r/BabyBumps/comments/abc123/title_slug/"
    thing_id, subreddit = parse_reddit_post_url(url)
    assert thing_id == "t3_abc123"
    assert subreddit == "BabyBumps"


def test_parse_old_reddit_url():
    url = "https://old.reddit.com/r/parenting/comments/xyz789/some_post"
    thing_id, subreddit = parse_reddit_post_url(url)
    assert thing_id == "t3_xyz789"
    assert subreddit == "parenting"


def test_parse_invalid_url_raises():
    with pytest.raises(ValueError, match="Invalid Reddit post URL"):
        parse_reddit_post_url("https://google.com")
