"""Reddit 发帖类型：auto + 5 种可选提示 + 标题多样性。"""
import random

from app.schemas.reddit import MANUAL_POST_TYPES, POST_TYPES
from app.services.ai_writer import (
    pick_title_style_hint,
    title_too_similar,
)
from app.services.reddit_mix import resolve_post_intent


def test_post_types_include_auto_and_manual_five():
    assert POST_TYPES[0] == "auto"
    assert MANUAL_POST_TYPES == (
        "pitfall",
        "vent",
        "unpopular",
        "guide",
        "help_seek",
    )
    assert set(POST_TYPES) == {"auto", *MANUAL_POST_TYPES}
    assert "consultation" not in POST_TYPES


def test_resolve_post_intent_follows_allow_product_switch():
    assert (
        resolve_post_intent(
            post_type="auto",
            community_purpose="promo",
            allow_product=False,
        )
        == "casual"
    )
    assert (
        resolve_post_intent(
            post_type="vent",
            community_purpose="persona",
            allow_product=True,
        )
        == "promo"
    )


def test_resolve_post_intent_legacy_without_allow_product():
    assert resolve_post_intent(post_type="vent", community_purpose="promo", include_site_url=True) == "casual"
    assert resolve_post_intent(post_type="guide", community_purpose="promo") == "promo"


def test_pick_title_style_hint_varies_with_seed():
    a = pick_title_style_hint("pitfall", rng=random.Random(1))
    b = pick_title_style_hint("pitfall", rng=random.Random(2))
    assert "Assigned title style" in a
    assert "Do NOT reuse" in a
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


def test_generate_reddit_post_auto_and_manual_prompts():
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
            allow_product=False,
            avoid_titles=["I wasted $80 on router upgrade"],
            rng=random.Random(3),
        )
        assert out["title"]
        assert captured and "r/Parenting" in captured[0]
        blob = captured[0].lower()
        assert "assigned title style" in blob
        assert "do not closely copy" in blob or "i wasted $80" in blob
        assert "zero product talk" in blob or "do not mention any brand" in blob
        if pt == "auto":
            assert "invent the format" in blob

    captured.clear()
    client.generate_reddit_post(
        "auto",
        "Parenting",
        "baby monitor",
        "https://example.com",
        allow_product=True,
        product_brief={"brand": "Acme", "product": "Monitor X"},
        rng=random.Random(1),
    )
    promo_blob = captured[0].lower()
    assert "product talk is allowed" in promo_blob
    assert "acme" in promo_blob
