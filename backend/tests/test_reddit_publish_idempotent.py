"""Reddit 发布幂等：外部 ID 修复、条件抢锁、失败可重试。"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from app.services import reddit_publish as pub
from app.services.reddit_client import RedditApiError


def _post(**kwargs):
    defaults = dict(
        id=1,
        site_id=1,
        account_id=1,
        subreddit="test",
        title="t",
        body="b",
        site_url=None,
        content_intent="casual",
        status="approved",
        reddit_post_id=None,
        reddit_permalink=None,
        published_at=None,
        error_message=None,
        scheduled_at=None,
        account=SimpleNamespace(id=1),
    )
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def _comment(**kwargs):
    defaults = dict(
        id=10,
        site_id=1,
        account_id=1,
        target_thing_id="t3_abc",
        subreddit="test",
        body="c",
        content_intent="casual",
        status="approved",
        reddit_comment_id=None,
        published_at=None,
        error_message=None,
        scheduled_at=None,
        account=SimpleNamespace(id=1),
    )
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


@pytest.fixture
def db():
    session = MagicMock()
    session.refresh = MagicMock()
    session.commit = MagicMock()
    return session


def test_repair_skips_api_when_external_id_exists(db):
    post = _post(status="failed", reddit_post_id="t3_already", error_message="timeout")
    with patch.object(pub, "get_reddit_client_for_account") as get_client:
        out = pub.publish_post_now(db, post, skip_risk_check=True)
    get_client.assert_not_called()
    assert out.status == "posted"
    assert out.error_message is None


def test_failed_can_retry_and_claim(db):
    post = _post(status="failed", error_message="boom")
    client = MagicMock()
    client.submit_post.return_value = {
        "name": "t3_new",
        "permalink": "/r/test/comments/new",
    }
    with (
        patch.object(pub, "enforce_publish_mix_quota"),
        patch.object(pub, "_claim_post", return_value=True),
        patch.object(pub, "get_reddit_client_for_account", return_value=client),
    ):
        out = pub.publish_post_now(db, post, skip_risk_check=True)
    client.submit_post.assert_called_once()
    assert out.status == "posted"
    assert out.reddit_post_id == "t3_new"


def test_concurrent_claim_loss_does_not_call_api(db):
    post = _post(status="approved")
    refreshes = {"n": 0}

    def refresh(_obj):
        refreshes["n"] += 1
        if refreshes["n"] >= 2:
            post.status = "posting"

    db.refresh.side_effect = refresh
    with (
        patch.object(pub, "enforce_publish_mix_quota"),
        patch.object(pub, "_claim_post", return_value=False),
        patch.object(pub, "get_reddit_client_for_account") as get_client,
    ):
        out = pub.publish_post_now(db, post, skip_risk_check=True)
    get_client.assert_not_called()
    assert out.status == "posting"


def test_api_error_marks_failed(db):
    post = _post(status="approved")
    client = MagicMock()
    client.submit_post.side_effect = RedditApiError("rate limit")
    with (
        patch.object(pub, "enforce_publish_mix_quota"),
        patch.object(pub, "_claim_post", return_value=True),
        patch.object(pub, "get_reddit_client_for_account", return_value=client),
    ):
        out = pub.publish_post_now(db, post, skip_risk_check=True)
    assert out.status == "failed"
    assert "rate limit" in (out.error_message or "")


def test_comment_repair_skips_api(db):
    comment = _comment(status="posting", reddit_comment_id="t1_x")
    with patch.object(pub, "get_reddit_client_for_account") as get_client:
        out = pub.publish_comment_now(db, comment, skip_risk_check=True)
    get_client.assert_not_called()
    assert out.status == "posted"
