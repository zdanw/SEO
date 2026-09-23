"""90/10 产品内容配额：近窗口内已发布 promo 不得超过 10%。"""
from app.services.reddit_mix import (
    MixQuotaExceeded,
    _COUNTED_STATUSES,
    can_enqueue_promo,
    enforce_promo_quota,
    resolve_post_intent,
)


def test_third_promo_among_twenty_is_rejected():
    """18 条人设 + 2 条产品后再加第 3 条产品应被拒。"""
    assert can_enqueue_promo(casual_count=18, promo_count=2) is False


def test_tenth_item_may_be_first_promo():
    """9 条人设后允许第 1 条产品（1/10 = 10%）。"""
    assert can_enqueue_promo(casual_count=9, promo_count=0) is True


def test_cannot_start_with_promo():
    assert can_enqueue_promo(casual_count=0, promo_count=0) is False


def test_casual_always_allowed():
    enforce_promo_quota(intent="casual", casual_count=0, promo_count=0)


def test_promo_raises_when_over_cap():
    try:
        enforce_promo_quota(intent="promo", casual_count=18, promo_count=2)
    except MixQuotaExceeded as exc:
        assert "10%" in str(exc)
        return
    raise AssertionError("expected MixQuotaExceeded")


def test_quota_counts_posted_only():
    assert "posted" in _COUNTED_STATUSES
    assert "pending_review" not in _COUNTED_STATUSES
    assert "approved" not in _COUNTED_STATUSES


def test_resolve_post_intent_vent_and_help_always_casual():
    assert resolve_post_intent(post_type="vent", community_purpose="promo", include_site_url=True) == "casual"
    assert resolve_post_intent(post_type="help_seek", community_purpose="promo") == "casual"


def test_resolve_post_intent_follows_community_for_other_types():
    assert resolve_post_intent(post_type="pitfall", community_purpose="persona") == "casual"
    assert resolve_post_intent(post_type="guide", community_purpose="promo") == "promo"
    assert resolve_post_intent(post_type="unpopular", community_purpose=None, include_site_url=True) == "promo"


def test_resolve_post_intent_allow_product_overrides_type():
    assert resolve_post_intent(post_type="vent", community_purpose="persona", allow_product=True) == "promo"
    assert resolve_post_intent(post_type="auto", community_purpose="promo", allow_product=False) == "casual"
