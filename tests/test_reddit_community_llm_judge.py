"""Zernio feed/search 取证 + LLM 批量判定（存在/活跃/人设）。"""
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any

from app.services.reddit_community_verify import (
    apply_verify_to_community,
    collect_subreddit_evidence,
    judge_communities_batch,
)


class _FakeClient:
    def __init__(self, feeds: dict[str, list[dict]], searches: dict[str, list[dict]] | None = None):
        self.feeds = feeds
        self.searches = searches or {}
        self.feed_calls: list[str] = []
        self.search_calls: list[tuple[str, str]] = []

    def list_feed(self, subreddit: str, limit: int = 10) -> list[dict]:
        self.feed_calls.append(subreddit)
        if subreddit == "MissingSub":
            err = RuntimeError("not found")
            err.status_code = 404  # type: ignore[attr-defined]
            raise err
        return list(self.feeds.get(subreddit, []))

    def search_posts(self, subreddit: str, keyword: str, limit: int = 10) -> list[dict]:
        self.search_calls.append((subreddit, keyword))
        return list(self.searches.get(subreddit, self.feeds.get(subreddit, [])))


class _FakeAI:
    def __init__(self, raw: str):
        self.raw = raw
        self.prompts: list[str] = []

    def chat(self, prompt: str, **kwargs: Any) -> str:
        self.prompts.append(prompt)
        return self.raw


def _post(title: str, *, hours_ago: float = 1, score: int = 10) -> dict:
    now = datetime(2026, 9, 19, 12, 0, tzinfo=timezone.utc).timestamp()
    return {
        "title": title,
        "score": score,
        "created_utc": int(now - hours_ago * 3600),
        "url": "https://reddit.com/r/x/comments/abc/hi/",
    }


def test_collect_evidence_uses_feed_and_search():
    client = _FakeClient(
        feeds={"Parenting": [_post("Sleep tips for newborns?")]},
        searches={"Parenting": [_post("Anyone else dealing with night feeds?")]},
    )
    evidence = collect_subreddit_evidence(
        ["Parenting"],
        client=client,
        search_keyword="parenting",
        now=datetime(2026, 9, 19, 12, 0, tzinfo=timezone.utc),
    )
    assert len(evidence) == 1
    assert evidence[0]["name"] == "Parenting"
    assert evidence[0]["feed_count"] == 1
    assert evidence[0]["search_count"] == 1
    assert evidence[0]["error"] is None
    assert client.feed_calls == ["Parenting"]
    assert client.search_calls == [("Parenting", "parenting")]


def test_judge_batch_llm_accepts_fit_and_rejects_unfit():
    evidence = [
        {
            "name": "Parenting",
            "feed_count": 3,
            "search_count": 2,
            "posts_7d": 3,
            "sample_titles": ["Newborn sleep help?", "Pumping schedule"],
            "error": None,
        },
        {
            "name": "WallStreetBets",
            "feed_count": 5,
            "search_count": 0,
            "posts_7d": 5,
            "sample_titles": ["YOLO calls", "Tendies"],
            "error": None,
        },
    ]
    ai = _FakeAI(
        '[{"name":"Parenting","ok":true,"exists":true,"active":true,"persona_fit":true,'
        '"score":0.9,"reason":"matches parenting interests"},'
        '{"name":"WallStreetBets","ok":false,"exists":true,"active":true,"persona_fit":false,'
        '"score":0.1,"reason":"finance meme sub, not parenting"}]'
    )
    results = judge_communities_batch(
        evidence,
        interests=["parenting", "newborn"],
        ai=ai,
    )
    by_name = {r.name: r for r in results}
    assert by_name["Parenting"].is_active_enough is True
    assert by_name["Parenting"].persona_fit is True
    assert by_name["Parenting"].exists is True
    assert by_name["WallStreetBets"].is_active_enough is False
    assert by_name["WallStreetBets"].persona_fit is False
    assert "parenting" in ai.prompts[0].lower()


def test_judge_batch_heuristic_without_ai():
    evidence = [
        {
            "name": "Parenting",
            "feed_count": 3,
            "search_count": 1,
            "posts_7d": 3,
            "sample_titles": ["Newborn sleep help for parents"],
            "error": None,
        },
        {
            "name": "EmptyDead",
            "feed_count": 0,
            "search_count": 0,
            "posts_7d": 0,
            "sample_titles": [],
            "error": "empty",
        },
    ]
    results = judge_communities_batch(evidence, interests=["parenting"], ai=None)
    by_name = {r.name: r for r in results}
    assert by_name["Parenting"].is_active_enough is True
    assert by_name["EmptyDead"].exists is False or by_name["EmptyDead"].is_active_enough is False


def test_apply_llm_reject_sets_inactive():
    row = SimpleNamespace(
        name="WallStreetBets",
        is_active=True,
        verified_at=None,
        exists=None,
        subscribers=None,
        accounts_active=None,
        posts_7d=None,
        activity_score=None,
        verify_error=None,
    )
    results = judge_communities_batch(
        [
            {
                "name": "WallStreetBets",
                "feed_count": 5,
                "search_count": 0,
                "posts_7d": 5,
                "sample_titles": ["YOLO"],
                "error": None,
            }
        ],
        interests=["parenting"],
        ai=_FakeAI(
            '[{"name":"WallStreetBets","ok":false,"exists":true,"active":true,'
            '"persona_fit":false,"score":0.1,"reason":"not relevant"}]'
        ),
    )
    apply_verify_to_community(row, results[0])
    assert row.is_active is False
    assert row.exists is True
    assert "not relevant" in (row.verify_error or "")
