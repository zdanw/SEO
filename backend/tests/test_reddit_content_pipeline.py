"""评论流水线：检测诊断驱动重写、mixed 风险、品牌闸、多轮分数。"""
from decimal import Decimal

from app.services.ai_detector import AiDetectionResult
from app.services.reddit_content import (
    build_brand_revision_notes,
    build_revision_notes,
    generate_comment_pipeline,
)


class _FakeAI:
    def __init__(self) -> None:
        self.calls: list[dict] = []
        self._bodies = [
            "This is a polished draft with em dashes and rule of three.",
            "tbh nights been rough for us too, same boat.",
        ]

    def generate_reddit_comment(self, **kwargs):
        self.calls.append(kwargs)
        idx = min(len(self.calls) - 1, len(self._bodies) - 1)
        return self._bodies[idx]


def test_build_revision_notes_includes_suggestions_and_sentences():
    detection = AiDetectionResult(
        score=Decimal("72"),
        verdict="likely_ai",
        engine="hybrid",
        lmscan={
            "high_risk_sentences": [
                {"text": "It is crucial to leverage synergies.", "score": 90},
            ]
        },
        signs_of_ai={
            "patterns": [
                {
                    "key": "em_dash_overuse",
                    "name": "破折号过度使用",
                    "suggestion": "Replace em dashes with commas.",
                    "matches": ["—"],
                }
            ]
        },
    )
    notes = build_revision_notes(detection)
    assert "likely_ai" in notes
    assert "Replace em dashes" in notes
    assert "leverage synergies" in notes


def test_pipeline_passes_revision_notes_on_rewrite(monkeypatch):
    ai = _FakeAI()
    scores = iter(
        [
            AiDetectionResult(score=Decimal("80"), verdict="likely_ai", engine="t"),
            AiDetectionResult(score=Decimal("80"), verdict="likely_ai", engine="t"),
            AiDetectionResult(score=Decimal("20"), verdict="human_like", engine="t"),
            AiDetectionResult(score=Decimal("18"), verdict="human_like", engine="t"),
        ]
    )

    def fake_detect(_text: str) -> AiDetectionResult:
        return next(scores)

    monkeypatch.setattr("app.services.reddit_content.detect_ai_content", fake_detect)
    monkeypatch.setattr(
        "app.services.reddit_content.humanize_comment",
        lambda text, **kw: text,
    )
    monkeypatch.setattr("app.services.reddit_content.record_detection_patterns", lambda *a, **k: None)

    out = generate_comment_pipeline(
        ai,  # type: ignore[arg-type]
        post_title="Night wakes?",
        post_body="Help",
        subreddit="Parenting",
        intent="casual",
        seed=1,
        site_id=1,
    )
    assert len(ai.calls) == 2
    assert ai.calls[0].get("revision_notes") in (None, "")
    assert ai.calls[1].get("revision_notes")
    assert "flagged" in (ai.calls[1]["revision_notes"] or "")
    assert out.ai_risk == "ok"
    assert out.rewrite_count == 1
    assert out.ai_score_before_humanize is not None
    assert out.ai_score_after_humanize is not None
    assert len(out.score_rounds) == 2


def test_pipeline_preserves_mixed_risk(monkeypatch):
    ai = _FakeAI()
    ai._bodies = ["kinda mixed tone but mostly fine honestly."]

    def fake_detect(_text: str) -> AiDetectionResult:
        return AiDetectionResult(score=Decimal("45"), verdict="mixed", engine="t")

    monkeypatch.setattr("app.services.reddit_content.detect_ai_content", fake_detect)
    monkeypatch.setattr(
        "app.services.reddit_content.humanize_comment",
        lambda text, **kw: text,
    )
    monkeypatch.setattr("app.services.reddit_content.record_detection_patterns", lambda *a, **k: None)

    out = generate_comment_pipeline(
        ai,  # type: ignore[arg-type]
        post_title="Hi",
        post_body="Hello",
        subreddit="Parenting",
        intent="casual",
        site_id=1,
    )
    assert out.ai_risk == "mixed"
    assert out.rewrite_count == 0


def test_brand_gate_sets_notes_and_brand_leak_risk(monkeypatch):
    ai = _FakeAI()
    ai._bodies = [
        "We love Bebcare at night honestly.",
        "Bebcare saved our sleep schedule.",
    ]

    monkeypatch.setattr(
        "app.services.reddit_content.detect_ai_content",
        lambda *_: (_ for _ in ()).throw(AssertionError("should not detect on brand leak")),
    )

    out = generate_comment_pipeline(
        ai,  # type: ignore[arg-type]
        post_title="Hi",
        post_body="Hello",
        subreddit="Parenting",
        intent="casual",
        product_brief={"brand": "Bebcare"},
        site_id=1,
    )
    assert out.ai_risk == "brand_leak"
    assert len(ai.calls) == 2
    assert "Bebcare" in (ai.calls[1].get("revision_notes") or "")
    assert "ZERO brand" in (ai.calls[1].get("revision_notes") or "")
    assert build_brand_revision_notes(["Bebcare"]).startswith("Your previous")


def test_generate_comment_prompt_includes_revision(monkeypatch):
    from app.services.ai_writer import DeepSeekClient

    client = DeepSeekClient(api_key="sk-test", base_url="https://example.invalid/v1", model="x")
    captured = {}

    def fake_chat(user_prompt, system_prompt="", temperature=0.7, max_tokens=4096):
        captured["user"] = user_prompt
        return "ok note taken"

    monkeypatch.setattr(client, "chat", fake_chat)
    client.generate_reddit_comment(
        post_title="Hi",
        post_body="Hello",
        subreddit="Parenting",
        revision_notes="Avoid em dash overuse.",
    )
    assert "Revision guidance" in captured["user"]
    assert "Avoid em dash overuse" in captured["user"]
