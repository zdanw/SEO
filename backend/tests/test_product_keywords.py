"""产品关键词规范化与替换辅助。"""
from app.services.reddit_discover import normalize_product_keywords


def test_normalize_product_keywords_caps_at_20():
    raw = [f"kw{i}" for i in range(25)]
    assert len(normalize_product_keywords(raw)) == 20


def test_normalize_product_keywords_trim_dedupe():
    assert normalize_product_keywords([" A ", "a", "", "B"]) == ["A", "B"]
