from datetime import datetime, timezone
from types import SimpleNamespace

from app.api.v1.reddit import _brief_from_product, _product_out


def test_brief_from_product_includes_product_name():
    brand = SimpleNamespace(name="Bebcare")
    product = SimpleNamespace(
        name="baby monitor",
        category="low EMF baby monitor",
        description="Analog baby monitor with no WiFi and low EMF.",
        talking_points=["no wifi", "analog"],
    )
    brief = _brief_from_product(brand, product)
    assert brief["brand"] == "Bebcare"
    assert brief["product"] == "baby monitor"
    assert brief["category"] == "low EMF baby monitor"
    assert brief["description"] == "Analog baby monitor with no WiFi and low EMF."
    assert brief["talking_points"] == ["no wifi", "analog"]


def test_product_out_includes_brand_name():
    now = datetime.now(timezone.utc)
    brand = SimpleNamespace(name="Bebcare")
    product = SimpleNamespace(
        id=1,
        brand_id=2,
        brand=brand,
        name="baby monitor",
        category="low EMF",
        description="",
        talking_points=[],
        is_active=True,
        communities=[],
        keyword_rows=[],
        created_at=now,
        updated_at=now,
    )
    out = _product_out(product)
    assert out.brand_id == 2
    assert out.brand_name == "Bebcare"
    assert out.name == "baby monitor"
