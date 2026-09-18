"""Reddit 生成走社区口吻 system prompt，人设评论不读产品简报。"""
from app.services.ai_writer import (
    DeepSeekClient,
    REDDIT_COMMENT_PROMPT,
    REDDIT_CONSULTATION_PROMPT,
    REDDIT_POST_SYSTEM_PROMPT,
    REDDIT_SYSTEM_PROMPT,
)


def test_reddit_comment_uses_reddit_system_prompt(monkeypatch):
    client = DeepSeekClient(api_key="sk-test", base_url="https://example.invalid/v1", model="x")
    captured = {}

    def fake_chat(user_prompt, system_prompt="", temperature=0.7, max_tokens=4096):
        captured["system"] = system_prompt
        captured["user"] = user_prompt
        return "I had the same nap regression last week."

    monkeypatch.setattr(client, "chat", fake_chat)
    body = client.generate_reddit_comment(
        post_title="Nap help?",
        post_body="Baby won't nap.",
        subreddit="Parenting",
        intent="casual",
        persona_prompt="Voice: working mom. Interests: parenting.",
    )
    assert captured["system"] == REDDIT_SYSTEM_PROMPT
    assert "working mom" in captured["user"]
    assert "Bebcare" not in captured["user"]
    assert body.startswith("I had")


def test_casual_comment_prompt_forbids_product():
    client = DeepSeekClient(api_key="sk-test", base_url="https://example.invalid/v1", model="x")
    captured = {}
    monkeypatch_chat = lambda user_prompt, system_prompt="", **kw: captured.setdefault("user", user_prompt) or "ok"
    client.chat = monkeypatch_chat  # type: ignore[method-assign]
    client.generate_reddit_comment(
        post_title="Hi",
        post_body="Hello",
        subreddit="Cooking",
        intent="casual",
        product_brief={"brand": "Bebcare", "talking_points": ["low EMF"]},
    )
    assert "Do not mention" in captured["user"] or "must not mention" in captured["user"].lower()
    assert "Bebcare" not in captured["user"] or "must not mention" in captured["user"].lower()


def test_prompts_prefer_casual_over_structured_guides():
    post_sys = REDDIT_POST_SYSTEM_PROMPT.lower()
    assert "no bullet lists" in post_sys
    assert "no tl;dr" in post_sys
    assert "em dash" in post_sys
    assert "25-80" in REDDIT_COMMENT_PROMPT
    assert "NO numbered lists" in REDDIT_CONSULTATION_PROMPT
    assert "80-180" in REDDIT_CONSULTATION_PROMPT
    for text in (REDDIT_SYSTEM_PROMPT, REDDIT_POST_SYSTEM_PROMPT, REDDIT_COMMENT_PROMPT, REDDIT_CONSULTATION_PROMPT):
        assert "\u2014" not in text
        assert "\u2013" not in text
