"""社区 few-shot 与检测反向 negative examples。"""
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


def test_negative_examples_from_detection_hits():
    reset_detection_patterns()
    record_detection_patterns(
        {
            "patterns": [
                {
                    "key": "em_dash_overuse",
                    "count": 3,
                    "matches": ["— frankly —"],
                },
                {
                    "key": "rule_of_three",
                    "count": 1,
                    "matches": ["calm, clear, and confident"],
                },
            ]
        }
    )
    record_detection_patterns(
        {
            "patterns": [
                {"key": "em_dash_overuse", "count": 2, "matches": ["—"]},
            ]
        }
    )
    block = format_negative_examples_block()
    assert "em_dash_overuse" in block
    assert "Hard avoid" in block
    system = with_negative_examples("BASE")
    assert system.startswith("BASE")
    assert "em_dash_overuse" in system
    reset_detection_patterns()
    assert format_negative_examples_block() == ""


def test_fetch_community_examples_fail_soft():
    class Boom:
        def list_feed(self, *_a, **_k):
            raise RuntimeError("down")

        def search_posts(self, *_a, **_k):
            raise RuntimeError("down")

    assert fetch_community_examples(Boom(), "Parenting") == []


def test_fetch_community_examples_from_feed():
    class Ok:
        def list_feed(self, subreddit, limit=10):
            return [
                {"title": f"t{i}", "body": f"b{i}", "score": i * 10}
                for i in range(1, 6)
            ]

    picked = fetch_community_examples(Ok(), "Parenting", limit=3)
    assert len(picked) == 3
    assert picked[0]["title"] == "t5"


def test_comment_prompt_includes_examples(monkeypatch):
    from app.services.ai_writer import DeepSeekClient

    client = DeepSeekClient(api_key="sk-test", base_url="https://example.invalid/v1", model="x")
    captured = {}

    def fake_chat(user_prompt, system_prompt="", **kw):
        captured["user"] = user_prompt
        captured["system"] = system_prompt
        return "ok"

    monkeypatch.setattr(client, "chat", fake_chat)
    reset_detection_patterns()
    record_detection_patterns(
        {"patterns": [{"key": "chatbot_phrase", "matches": ["hope this helps"]}]}
    )
    client.generate_reddit_comment(
        post_title="Hi",
        post_body="Hello",
        subreddit="Parenting",
        community_examples=[{"title": "Anyone else exhausted", "body": "3am again"}],
    )
    assert "Anyone else exhausted" in captured["user"]
    assert "chatbot_phrase" in captured["system"]
    reset_detection_patterns()
