"""智能发现：社区配额抽样、帖过滤、问句优先。"""
from datetime import datetime, timezone

from app.services.reddit_discover import (
    filter_commentable,
    pick_discover_targets,
    pick_promo_search_keyword,
    prefer_product_relevant,
    prefer_questions,
    product_search_terms,
    run_smart_discover,
    suggest_persona_subreddits,
)

def test_suggest_persona_subreddits_uses_interest_fallback():
    names = suggest_persona_subreddits(["parenting", "cooking"])
    assert "Parenting" in names
    assert "Cooking" in names


def test_pick_three_persona_and_one_promo():
    persona = [f"Hobby{i}" for i in range(12)]
    promo = ["BabyGear", "EMFSafety"]
    picked = pick_discover_targets(persona, promo, seed=3)
    intents = [intent for _, intent in picked]
    assert intents.count("promo") <= 1
    assert intents.count("casual") == 3
    assert len({name for name, intent in picked if intent == "casual"}) == 3
    assert all(name in persona for name, intent in picked if intent == "casual")
    assert all(name in promo for name, intent in picked if intent == "promo")


def test_pick_does_not_repeat_when_few_communities():
    picked = pick_discover_targets(["Parenting", "Mommit"], ["BabyGear"], seed=1)
    casual = [name for name, intent in picked if intent == "casual"]
    assert set(casual) == {"Parenting", "Mommit"}
    assert len(casual) == 2


def test_run_smart_discover_stops_on_rate_limit():
    calls: list[str] = []

    def boom(subreddit, keyword, limit):
        calls.append(subreddit)
        err = RuntimeError("Zernio 429")
        err.status_code = 429  # type: ignore[attr-defined]
        raise err

    result = run_smart_discover(
        persona_communities=["Parenting", "Mommit", "daddit"],
        promo_communities=[],
        already_commented=set(),
        search_fn=boom,
        generate_fn=lambda *_: None,
        allow_promo=False,
        remaining_slots=3,
        seed=1,
    )
    assert calls == ["Parenting"] or len(calls) == 1
    assert result["reason"] == "upstream"


def test_skip_promo_when_quota_blocks():
    picked = pick_discover_targets(["Parenting"], ["BabyGear"], seed=1, allow_promo=False)
    assert all(intent == "casual" for _, intent in picked)


def test_filter_skips_old_and_already_commented():
    now = datetime(2026, 9, 17, 8, 0, tzinfo=timezone.utc)
    fresh = {
        "url": "https://reddit.com/r/x/comments/aaa/hi/",
        "title": "anyone else up at 3am?",
        "score": 4,
        "created_utc": int(now.timestamp()) - 3600,
    }
    old = {
        "url": "https://reddit.com/r/x/comments/bbb/old/",
        "title": "old thread",
        "score": 10,
        "created_utc": int(now.timestamp()) - 80 * 3600,
    }
    kept = filter_commentable(
        [fresh, old, {"url": fresh["url"], "title": "dup", "score": 2, "created_utc": fresh["created_utc"]}],
        already_commented={"https://reddit.com/r/x/comments/aaa/hi/"},
        now=now,
    )
    assert kept == []

    kept2 = filter_commentable([fresh, old], already_commented=set(), now=now)
    assert [i["url"] for i in kept2] == [fresh["url"]]


def test_prefer_questions_first():
    items = [
        {"title": "Just sharing a win", "url": "a"},
        {"title": "help with night wakes?", "url": "b"},
        {"title": "Anyone tried a floor bed", "url": "c"},
    ]
    ranked = prefer_questions(items)
    assert ranked[0]["url"] == "b"
    assert {i["url"] for i in ranked[:2]} == {"b", "c"}


def test_run_smart_discover_no_communities():
    result = run_smart_discover(
        persona_communities=[],
        promo_communities=[],
        already_commented=set(),
        search_fn=lambda *_: [],
        generate_fn=lambda *_: None,
        allow_promo=True,
        remaining_slots=8,
    )
    assert result["queued"] == 0
    assert result["reason"] == "no_communities"


def test_run_smart_discover_reports_upstream_error():
    def boom(subreddit, keyword, limit):
        raise RuntimeError("Zernio 404: Account not found")

    result = run_smart_discover(
        persona_communities=["Parenting"],
        promo_communities=[],
        already_commented=set(),
        search_fn=boom,
        generate_fn=lambda *_: None,
        allow_promo=True,
        remaining_slots=8,
        seed=1,
    )
    assert result["queued"] == 0
    assert result["errors"] >= 1
    assert result["reason"] == "upstream"
    assert "Account not found" in result["last_error"]


def test_run_smart_discover_uses_feed_items():
    now = int(datetime.now(timezone.utc).timestamp())
    generated: list[str] = []

    def feed(subreddit, keyword, limit):
        return [{
            "title": "anyone else up at 3am?",
            "url": f"https://www.reddit.com/r/{subreddit}/comments/aaa/hi/",
            "thing_id": "t3_aaa",
            "subreddit": subreddit,
            "score": 4,
            "created_utc": now - 600,
            "body": "",
        }]

    result = run_smart_discover(
        persona_communities=["Parenting"],
        promo_communities=[],
        already_commented=set(),
        search_fn=feed,
        generate_fn=lambda item, intent: generated.append(item["url"]),
        allow_promo=True,
        remaining_slots=2,
        seed=1,
        now=datetime.now(timezone.utc),
    )
    assert result["queued"] >= 1
    assert generated


def test_run_smart_discover_manual_targets_one_per_community():
    now = int(datetime.now(timezone.utc).timestamp())
    searched: list[str] = []
    generated: list[tuple[str, str]] = []

    def feed(subreddit, keyword, limit):
        searched.append(subreddit)
        title = (
            "help with baby gear picks?"
            if subreddit == "BabyGear"
            else "help with night wakes?"
        )
        return [{
            "title": title,
            "url": f"https://www.reddit.com/r/{subreddit}/comments/{subreddit.lower()}/hi/",
            "thing_id": f"t3_{subreddit}",
            "subreddit": subreddit,
            "score": 4,
            "created_utc": now - 600,
            "body": "",
        }]

    result = run_smart_discover(
        persona_communities=[],
        promo_communities=[],
        already_commented=set(),
        search_fn=feed,
        generate_fn=lambda item, intent: generated.append((item["subreddit"], intent)),
        allow_promo=True,
        remaining_slots=5,
        targets=[("Parenting", "casual"), ("BabyGear", "promo")],
        product_terms=["baby gear"],
        now=datetime.now(timezone.utc),
    )
    assert result["queued"] == 2
    assert searched == ["Parenting", "BabyGear"]
    assert generated == [("Parenting", "casual"), ("BabyGear", "promo")]


def test_product_search_terms_from_keywords_only():
    terms = product_search_terms(keywords=[" Baby Monitors ", "EMF", "Baby Monitors", ""])
    assert terms == ["Baby Monitors", "EMF"]


def test_pick_promo_search_keyword_uses_seed():
    terms = ["a", "b", "c"]
    assert pick_promo_search_keyword(terms, seed=7) == pick_promo_search_keyword(terms, seed=7)
    assert pick_promo_search_keyword([], seed=1) == ""


def test_prefer_product_relevant_drops_unrelated():
    items = [
        {"title": "anyone else tired today?", "body": "", "url": "a"},
        {"title": "best baby monitors for newborns?", "body": "", "url": "b"},
        {"title": "wifi baby monitors EMF worries", "body": "", "url": "c"},
    ]
    kept = prefer_product_relevant(items, ["Baby Monitors", "EMF"])
    assert {i["url"] for i in kept} == {"b", "c"}
    assert kept[0]["url"] == "b"  # 问句优先
    assert all(i["url"] != "a" for i in kept)


def test_run_smart_discover_promo_skips_when_no_product_terms():
    calls: list[tuple[str, str]] = []

    def feed(subreddit, keyword, limit):
        calls.append((subreddit, keyword))
        return []

    result = run_smart_discover(
        persona_communities=[],
        promo_communities=[],
        already_commented=set(),
        search_fn=feed,
        generate_fn=lambda *_: None,
        allow_promo=True,
        remaining_slots=3,
        targets=[("Buyingforbaby", "promo")],
        product_terms=[],
        now=datetime.now(timezone.utc),
    )
    assert calls == []
    assert result["queued"] == 0


def test_run_smart_discover_promo_skips_unrelated_posts():
    now = int(datetime.now(timezone.utc).timestamp())
    generated: list[str] = []

    def feed(subreddit, keyword, limit):
        assert keyword == "Baby Monitors"
        return [
            {
                "title": "random parenting rant",
                "url": "https://www.reddit.com/r/x/comments/aaa/hi/",
                "thing_id": "t3_aaa",
                "subreddit": subreddit,
                "score": 4,
                "created_utc": now - 600,
                "body": "",
            },
            {
                "title": "which baby monitors are quiet at night?",
                "url": "https://www.reddit.com/r/x/comments/bbb/hi/",
                "thing_id": "t3_bbb",
                "subreddit": subreddit,
                "score": 4,
                "created_utc": now - 500,
                "body": "",
            },
        ]

    result = run_smart_discover(
        persona_communities=[],
        promo_communities=[],
        already_commented=set(),
        search_fn=feed,
        generate_fn=lambda item, intent: generated.append(item["url"]),
        allow_promo=True,
        remaining_slots=3,
        seed=1,
        targets=[("Buyingforbaby", "promo")],
        product_terms=["Baby Monitors"],
        now=datetime.now(timezone.utc),
    )
    assert result["queued"] == 1
    assert generated == ["https://www.reddit.com/r/x/comments/bbb/hi/"]
