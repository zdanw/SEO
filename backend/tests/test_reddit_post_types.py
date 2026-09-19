"""Reddit 发帖类型：5 种原生帖型 + 标题多样性。"""
import random

from app.schemas.reddit import POST_TYPES
from app.services.ai_writer import (
    pick_title_style_hint,
    title_too_similar,
)


def test_post_types_are_native_five():
    assert POST_TYPES == (
        "pitfall",
        "vent",
        "unpopular",
        "guide",
        "help_seek",
    )
    allowed: set[str] = set(POST_TYPES)
    assert "consultation" not in allowed
    assert "experience" not in allowed


def test_pick_title_style_hint_varies_with_seed():
    a = pick_title_style_hint("pitfall", rng=random.Random(1))
    b = pick_title_style_hint("pitfall", rng=random.Random(2))
    assert "Assigned title style" in a
    assert "Do NOT reuse" in a
    # 不同种子通常抽到不同句式（极小概率相同，再试一组）
    if a == b:
        c = pick_title_style_hint("pitfall", rng=random.Random(99))
        assert a != c or b != c


def test_title_too_similar_detects_same_opener():
    recent = ["I wasted $80 on router upgrade cables"]
    assert title_too_similar("I wasted $150 on a gaming headset", recent) is True
    assert title_too_similar("Three returns later and I still hate this router mess", recent) is False


def test_post_topic_coherent_requires_shared_topic():
    from app.services.ai_writer import post_topic_coherent

    assert (
        post_topic_coherent(
            "three returns later my dignity is gone",
            "I bought a fancy gaming router then a mesh system...",
            "baby monitor",
        )
        is False
    )
    assert (
        post_topic_coherent(
            "three returns later on this baby monitor rabbit hole",
            "I kept returning baby monitors that chirped all night...",
            "baby monitor",
        )
        is True
    )


def test_generate_reddit_post_dispatches_prompts():
    from app.services import ai_writer

    captured: list[str] = []

    class FakeClient(ai_writer.DeepSeekClient):
        def __init__(self):
            pass

        def chat(self, user_prompt, system_prompt="", temperature=0.7, max_tokens=4096):
            captured.append(user_prompt)
            return '{"title": "Quiet exhaustion about baby gear tonight", "body": "b"}'

    client = FakeClient()
    for pt in POST_TYPES:
        captured.clear()
        out = client.generate_reddit_post(
            pt,
            "Parenting",
            "baby monitor",
            None,
            avoid_titles=["I wasted $80 on router upgrade"],
            rng=random.Random(3),
        )
        assert out["title"]
        assert captured and "r/Parenting" in captured[0]
        blob = captured[0].lower()
        assert "assigned title style" in blob
        assert "do not closely copy" in blob or "i wasted $80" in blob
        if pt == "vent":
            assert "product" in blob and (
                "do not mention" in blob or "never mention" in blob or "no product" in blob
            )
