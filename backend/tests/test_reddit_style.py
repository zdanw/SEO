"""社区 few-shot 与按站点隔离的检测反向 negative examples。"""
from app.services.reddit_style import (
    fetch_community_examples,
    format_community_examples_block,
    format_negative_examples_block,
    pick_community_examples,
    record_detection_patterns,
    redact_brand_text,
    reset_detection_patterns,
    with_negative_examples,
)


def test_pick_community_examples_by_score():
    items = [
        {"title": "low", "body": "a", "score": 2},
        {"title": "high", "body": "best sleep tip ever", "score": 90},
        {"title": "mid", "body": "ok", "score": 20},
    ]
    picked = pick_community_examples(items, limit=2)
    assert [p["title"] for p in picked] == ["high", "mid"]
    assert "sleep" in picked[0]["body"]


def test_format_community_examples_block():
    block = format_community_examples_block(
        [{"title": "Night wakes again", "body": "kid up at 3am"}],
        for_post=True,
    )
    assert "Night wakes again" in block
    assert "do NOT copy" in block


def test_redact_brand_text():
    assert "[brand]" in redact_brand_text("love Bebcare overnight", {"Bebcare"})
    assert "Bebcare" not in redact_brand_text("love Bebcare overnight", {"Bebcare"})


def test_negative_examples_are_site_scoped_with_redacted_samples(monkeypatch):
    monkeypatch.setattr("app.services.reddit_style._redis", lambda: None)
    reset_detection_patterns()
    record_detection_patterns(
        {
            "patterns": [
                {
                    "key": "em_dash_overuse",
                    "count": 3,
                    "matches": ["Bebcare — best ever"],
                },
            ]
        },
        site_id=1,
        brand_names=["Bebcare"],
    )
    record_detection_patterns(
        {
            "patterns": [
                {"key": "chatbot_phrase", "count": 2, "matches": ["hope this helps"]},
            ]
        },
        site_id=2,
    )
    block1 = format_negative_examples_block(site_id=1)
    block2 = format_negative_examples_block(site_id=2)
    assert "em_dash_overuse" in block1
    assert "[brand]" in block1
    assert "Bebcare" not in block1
    assert "chatbot_phrase" not in block1
    assert "chatbot_phrase" in block2
    assert "hope this helps" in block2
    assert "em_dash_overuse" not in block2
    system = with_negative_examples("BASE", site_id=1)
    assert system.startswith("BASE")
    assert "em_dash_overuse" in system
    reset_detection_patterns(site_id=1)
    reset_detection_patterns(site_id=2)
    assert format_negative_examples_block(site_id=1) == ""


def test_record_without_site_id_is_noop(monkeypatch, caplog):
    monkeypatch.setattr("app.services.reddit_style._redis", lambda: None)
    reset_detection_patterns()
    with caplog.at_level("WARNING"):
        record_detection_patterns(
            {"patterns": [{"key": "em_dash_overuse", "matches": ["—"]}]},
            site_id=None,
        )
    assert format_negative_examples_block(site_id=1) == ""
    assert any("site_id is None" in r.message for r in caplog.records)


def test_fetch_community_examples_fail_soft():
    class Boom:
        def list_feed(self, *_a, **_k):
            raise RuntimeError("down")

        def search_posts(self, *_a, **_k):
            raise RuntimeError("down")

    assert fetch_community_examples(Boom(), "Parenting") == []


def test_fetch_community_examples_caches_and_purges(monkeypatch):
    monkeypatch.setattr("app.services.reddit_style._redis", lambda: None)
    calls = {"n": 0}

    class Ok:
        def list_feed(self, subreddit, limit=10):
            calls["n"] += 1
            return [{"title": "t5", "body": "b5", "score": 50}]

    from app.services import reddit_style as style

    style._mem_examples.clear()
    a = fetch_community_examples(Ok(), "ParentingCacheTest", limit=1)
    b = fetch_community_examples(Ok(), "ParentingCacheTest", limit=1)
    assert a == b
    assert calls["n"] == 1

    # 过期条目在读取时被清理
    style._mem_examples["stale_sub"] = (0, [{"title": "old", "body": "x"}])
    assert style._load_cached_examples("stale_sub") is None
    assert "stale_sub" not in style._mem_examples


def test_comment_prompt_includes_examples(monkeypatch):
    from app.services.ai_writer import DeepSeekClient

    monkeypatch.setattr("app.services.reddit_style._redis", lambda: None)
    client = DeepSeekClient(api_key="sk-test", base_url="https://example.invalid/v1", model="x")
    captured = {}

    def fake_chat(user_prompt, system_prompt="", **kw):
        captured["user"] = user_prompt
        captured["system"] = system_prompt
        return "ok"

    monkeypatch.setattr(client, "chat", fake_chat)
    reset_detection_patterns(site_id=9)
    record_detection_patterns(
        {"patterns": [{"key": "chatbot_phrase", "matches": ["hope this helps"]}]},
        site_id=9,
    )
    client.generate_reddit_comment(
        post_title="Hi",
        post_body="Hello",
        subreddit="Parenting",
        community_examples=[{"title": "Anyone else exhausted", "body": "3am again"}],
        site_id=9,
    )
    assert "Anyone else exhausted" in captured["user"]
    assert "chatbot_phrase" in captured["system"]
    assert "hope this helps" in captured["system"]
    reset_detection_patterns(site_id=9)
