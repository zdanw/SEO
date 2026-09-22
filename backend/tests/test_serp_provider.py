"""P2 SERP provider / 状态语义 / 配额。"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from app.services.serp_provider import (
    SerpFetchResult,
    normalize_fetch_result,
    resolve_provider_name,
    try_consume_serp_quota,
)


def test_normalize_success_with_rank():
    r = SerpFetchResult(
        keyword="k",
        region="us",
        target_rank=3,
        target_page=1,
        crawl_status="success",
        provider="mock",
    )
    out = normalize_fetch_result(r, "https://example.com")
    assert out.crawl_status == "success"
    assert out.serp_features["provider"] == "mock"


def test_normalize_not_found_when_target_missing():
    r = SerpFetchResult(
        keyword="k",
        region="us",
        target_rank=None,
        crawl_status="success",
        provider="scrapingbee",
    )
    out = normalize_fetch_result(r, "https://example.com/page")
    assert out.crawl_status == "not_found"
    assert out.target_rank is None


def test_resolve_provider_prefers_scrapingbee(monkeypatch):
    from app.core import config

    monkeypatch.setattr(config.settings, "SCRAPINGBEE_API_KEY", "key")
    monkeypatch.setattr(config.settings, "SERP_ALLOW_PROXY_FALLBACK", True)
    monkeypatch.setattr(config.settings, "PROXY_LIST", "http://x")
    assert resolve_provider_name() == "scrapingbee"


def test_resolve_provider_proxy_requires_flag(monkeypatch):
    from app.core import config

    monkeypatch.setattr(config.settings, "SCRAPINGBEE_API_KEY", None)
    monkeypatch.setattr(config.settings, "SERP_ALLOW_PROXY_FALLBACK", False)
    monkeypatch.setattr(config.settings, "PROXY_LIST", "http://x")
    assert resolve_provider_name() == "mock"

    monkeypatch.setattr(config.settings, "SERP_ALLOW_PROXY_FALLBACK", True)
    assert resolve_provider_name() == "proxy"


def test_try_consume_serp_quota_blocks_when_over(monkeypatch):
    from app.core import config

    monkeypatch.setattr(config.settings, "SERP_DAILY_QUOTA", 2)
    monkeypatch.setattr(config.settings, "SERP_EST_COST_PER_REQUEST", 0.01)
    redis = MagicMock()
    # first call used=1, second used=2, third used=3 then decr
    redis.incrby.side_effect = [1, 2, 3]
    with patch("app.services.serp_provider.get_redis", return_value=redis):
        assert try_consume_serp_quota() is True
        assert try_consume_serp_quota() is True
        assert try_consume_serp_quota() is False
    redis.decrby.assert_called_once()
