"""站点级跨账号查重。"""
from types import SimpleNamespace
from unittest.mock import MagicMock

from app.models.reddit import RedditComment
from app.services.reddit_risk import (
    SIMILARITY_THRESHOLD,
    SimilarityHit,
    _text_similarity,
    find_similar_on_site,
)


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

    hit = find_similar_on_site(
        db,
        model=RedditComment,
        site_id=1,
        body=body,
        exclude_id=1,
        kind_label="评论",
    )
    assert isinstance(hit, SimilarityHit)
    assert hit.other_id == 42
    assert hit.account_id == 99
    assert hit.ratio >= SIMILARITY_THRESHOLD
    assert "99" in hit.message
    assert "42" in hit.message


def test_find_similar_skips_short_body():
    db = MagicMock()
    assert find_similar_on_site(db, model=RedditComment, site_id=1, body="too short") is None
    db.query.assert_not_called()


def test_text_similarity_token_aware():
    a = "nights are really hard with the baby monitor setup"
    b = "nights are truly tough with the baby monitor setup"
    assert _text_similarity(a, b) >= 0.7
