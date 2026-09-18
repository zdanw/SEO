"""品牌/产品库：brief 从品牌+产品组装。"""
from types import SimpleNamespace

from app.api.v1.reddit import _brief_from_product


def test_brief_from_product_includes_product_name():
    brand = SimpleNamespace(name="Bebcare")
    product = SimpleNamespace(
        name="baby monitor",
        category="low EMF baby monitor",
        talking_points=["no wifi", "analog"],
    )
    brief = _brief_from_product(brand, product)
    assert brief["brand"] == "Bebcare"
    assert brief["product"] == "baby monitor"
    assert brief["category"] == "low EMF baby monitor"
    assert brief["talking_points"] == ["no wifi", "analog"]
