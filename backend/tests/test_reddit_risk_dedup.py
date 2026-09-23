"""站点级跨账号查重。"""
from types import SimpleNamespace
from unittest.mock import MagicMock

from app.models.reddit import RedditComment
from app.services.reddit_risk import SIMILARITY_THRESHOLD, find_similar_on_site


class _FakeQuery:
    def __init__(self, rows):
        self._rows = rows

    def filter(self, *args, **kwargs):
        return self

    def order_by(self, *args, **kwargs):
        return self

    def limit(self, n):
        self._rows = self._rows[:n]
        return self

    def all(self):
        return self._rows


def test_find_similar_on_site_catches_other_account():
    body = (
        "We tried three different white noise machines and nothing helped until "
        "we moved the crib away from the window last week honestly."
    )
    other = SimpleNamespace(
        id=42,
        account_id=99,
        body=body,
    )
    db = MagicMock()
    db.query.return_value = _FakeQuery([other])

    msg = find_similar_on_site(
        db,
        model=RedditComment,
        site_id=1,
        body=body,
        exclude_id=1,
        kind_label="评论",
    )
    assert msg is not None
    assert "99" in msg
    assert "42" in msg
    assert "100%" in msg or f"{int(SIMILARITY_THRESHOLD * 100)}" in msg


def test_find_similar_skips_short_body():
    db = MagicMock()
    assert find_similar_on_site(db, model=RedditComment, site_id=1, body="too short") is None
    db.query.assert_not_called()
