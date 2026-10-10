"""产品绑定社区：智能发现 promo 池只抽绑定社区。"""
from app.services.reddit_discover import pick_discover_targets, promo_names_for_product


def test_promo_names_for_product_intersection():
    bound = {"BabyBumps", "Buyingforbaby", "Parenting"}
    active_promo = ["BabyBumps", "Pumping", "Buyingforbaby"]
    names = promo_names_for_product(active_promo, bound)
    assert set(names) == {"BabyBumps", "Buyingforbaby"}


def test_pick_discover_uses_only_bound_promo():
    picked = pick_discover_targets(
        ["Mommit", "daddit", "Parenting"],
        ["BabyBumps", "Buyingforbaby"],
        seed=1,
        allow_promo=True,
    )
    promo = [n for n, intent in picked if intent == "promo"]
    assert len(promo) <= 1
    assert all(n in {"BabyBumps", "Buyingforbaby"} for n in promo)


def test_promo_names_empty_when_no_binding():
    assert promo_names_for_product(["BabyBumps"], set()) == []
    assert promo_names_for_product(["BabyBumps"], None) == []
