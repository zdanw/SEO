"""P4：卡住的 posting 超时回收与强制失败。"""
from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock

from app.services import reddit_publish as pub


def test_force_fail_posting_without_external_id():
    post = SimpleNamespace(
        id=1,
        status="posting",
        reddit_post_id=None,
        error_message=None,
        updated_at=datetime.utcnow() - timedelta(minutes=30),
    )
    db = MagicMock()
    out = pub.force_fail_post(db, post, reason="手动标记失败")
    assert out.status == "failed"
    assert "手动标记失败" in (out.error_message or "")
    db.commit.assert_called()


def test_force_fail_refuses_when_external_id_exists():
    post = SimpleNamespace(
        id=1,
        status="posting",
        reddit_post_id="t3_x",
        error_message=None,
    )
    db = MagicMock()
    try:
        pub.force_fail_post(db, post)
        assert False, "expected ValueError"
    except ValueError as exc:
        assert "外部" in str(exc) or "已发布" in str(exc)


def test_recover_stale_posting_marks_only_cutoff_matches():
    stale = SimpleNamespace(
        id=1,
        status="posting",
        reddit_post_id=None,
        error_message=None,
        updated_at=datetime.utcnow() - timedelta(minutes=20),
    )
    db = MagicMock()

    post_q = MagicMock()
    post_q.filter.return_value = post_q
    post_q.all.return_value = [stale]

    comment_q = MagicMock()
    comment_q.filter.return_value = comment_q
    comment_q.all.return_value = []

    def query_side(model):
        if model is pub.RedditPost:
            return post_q
        return comment_q

    db.query.side_effect = query_side

    n = pub.recover_stale_posting(db, timeout_minutes=15)
    assert n == 1
    assert stale.status == "failed"
    assert "超时" in (stale.error_message or "")
    db.commit.assert_called()
