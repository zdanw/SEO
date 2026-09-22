"""发帖：从产品关键词随机选题。"""
from app.services.reddit_discover import pick_promo_search_keyword, product_search_terms


def test_post_keyword_from_product_terms():
    terms = product_search_terms(keywords=["low EMF", "baby monitor"])
    picked = pick_promo_search_keyword(terms, seed=3)
    assert picked in terms


def test_empty_product_keywords_means_no_topic():
    assert product_search_terms(keywords=[]) == []
    assert pick_promo_search_keyword([], seed=1) == ""
