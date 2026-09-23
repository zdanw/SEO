"""社区 few-shot 与按站点隔离的检测反向 negative examples。"""
from app.services.reddit_style import (
    fetch_community_examples,
    format_community_examples_block,
    format_negative_examples_block,
    pick_community_examples,
    record_detection_patterns,
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


def test_negative_examples_are_site_scoped(monkeypatch):
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
    assert "Bebcare" not in block1  # 不注入原文，避免品牌泄漏
    assert "chatbot_phrase" not in block1
    assert "chatbot_phrase" in block2
    assert "em_dash_overuse" not in block2
    system = with_negative_examples("BASE", site_id=1)
    assert system.startswith("BASE")
    assert "em_dash_overuse" in system
    reset_detection_patterns(site_id=1)
    reset_detection_patterns(site_id=2)
    assert format_negative_examples_block(site_id=1) == ""


def test_record_without_site_id_is_noop(monkeypatch):
    monkeypatch.setattr("app.services.reddit_style._redis", lambda: None)
    reset_detection_patterns()
    record_detection_patterns(
        {"patterns": [{"key": "em_dash_overuse", "matches": ["—"]}]},
        site_id=None,
    )
    assert format_negative_examples_block(site_id=1) == ""


def test_fetch_community_examples_fail_soft():
    class Boom:
        def list_feed(self, *_a, **_k):
            raise RuntimeError("down")

        def search_posts(self, *_a, **_k):
            raise RuntimeError("down")

    assert fetch_community_examples(Boom(), "Parenting") == []


def test_fetch_community_examples_caches(monkeypatch):
    monkeypatch.setattr("app.services.reddit_style._redis", lambda: None)
    calls = {"n": 0}

    class Ok:
        def list_feed(self, subreddit, limit=10):
            calls["n"] += 1
            return [{"title": "t5", "body": "b5", "score": 50}]

    # 清内存缓存
    from app.services import reddit_style as style

    style._mem_examples.clear()
    a = fetch_community_examples(Ok(), "ParentingCacheTest", limit=1)
    b = fetch_community_examples(Ok(), "ParentingCacheTest", limit=1)
    assert a == b
    assert calls["n"] == 1


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
    reset_detection_patterns(site_id=9)
