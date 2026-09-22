"""Google SERP 爬虫服务（实现 SERPProvider 契约）。

模式（合规优先）：
1. ScrapingBee（SCRAPINGBEE_API_KEY）
2. 代理直抓（仅当 SERP_ALLOW_PROXY_FALLBACK=true 且配置了代理）
3. Mock（以上均不可用）

业务任务应通过 get_serp_crawler().crawl() 或 Provider.fetch() 调用，
不要绑定某一供应商实现。
"""
from __future__ import annotations

import hashlib
import logging
import random
import re
import time
from datetime import datetime
from typing import Optional

import httpx

from app.core.config import settings
from app.services.serp_provider import (
    SERPProvider,
    SerpFetchResult,
    SerpQuery,
    normalize_fetch_result,
    resolve_provider_name,
    try_consume_serp_quota,
)
from app.utils.proxy import get_proxy_pool
from app.utils.rate_limiter import (
    CircuitBreaker,
    CircuitBreakerOpen,
    RateLimitExceeded,
    acquire_token,
)

logger = logging.getLogger(__name__)

GOOGLE_SEARCH_URL = "https://www.google.com/search"
SCRAPINGBEE_GOOGLE_URL = "https://app.scrapingbee.com/api/v1/google"
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:131.0) Gecko/20100101 Firefox/131.0",
]

# 向后兼容旧名称
SerpResult = SerpFetchResult


class SerpCrawlError(RuntimeError):
    """SERP 抓取基础错误。"""


class SerpBlockedError(SerpCrawlError):
    """触发验证码/IP 被封。"""


class SerpTimeoutError(SerpCrawlError):
    """请求超时。"""


class SerpQuotaExceeded(SerpCrawlError):
    """日采集配额已用尽。"""


class SerpCrawler:
    """SERP 爬虫主类，同时实现 SERPProvider。"""

    name = "serp_crawler"

    def __init__(self) -> None:
        self._provider_name = resolve_provider_name()
        self._pool = get_proxy_pool()

    @property
    def is_mock(self) -> bool:
        return self._provider_name == "mock"

    @property
    def is_scrapingbee(self) -> bool:
        return self._provider_name == "scrapingbee"

    @property
    def is_proxy(self) -> bool:
        return self._provider_name == "proxy"

    def fetch(self, query: SerpQuery) -> SerpFetchResult:
        return self.crawl(
            keyword=query.keyword,
            target_url=query.target_url,
            region=query.region,
            competitor_domains=query.competitor_domains,
            language=query.language,
            device=query.device,
        )

    def crawl(
        self,
        keyword: str,
        target_url: Optional[str],
        region: str = "us",
        competitor_domains: list[str] | None = None,
        language: str = "en",
        device: str = "desktop",
    ) -> SerpFetchResult:
        """抓取单关键词 SERP。"""
        if not try_consume_serp_quota(1):
            raise SerpQuotaExceeded("SERP 日配额已用尽，请明天再试或提高 SERP_DAILY_QUOTA")

        if not acquire_token("serp", keyword):
            raise RateLimitExceeded(f"SERP 抓取 {keyword} 被限流")
        breaker = CircuitBreaker("serp", keyword)
        if not breaker.allow_request():
            raise CircuitBreakerOpen(f"SERP 抓取 {keyword} 熔断器打开")

        started = time.perf_counter()
        try:
            if self._provider_name == "scrapingbee":
                result = self._scrapingbee_crawl(keyword, target_url, region, language)
            elif self._provider_name == "proxy":
                result = self._real_crawl(keyword, target_url, region, language)
            else:
                result = self._mock_crawl(keyword, target_url, region, language)
            result.device = device
            result.language = language
            result.duration_ms = int((time.perf_counter() - started) * 1000)
            if result.estimated_cost is None:
                result.estimated_cost = float(settings.SERP_EST_COST_PER_REQUEST)
            result = normalize_fetch_result(result, target_url)
            breaker.record_success()
            if competitor_domains and result.organic_results:
                result.serp_features["competitor_ranks"] = self._match_competitors(
                    result.organic_results, competitor_domains
                )
            return result
        except (SerpBlockedError, SerpTimeoutError, SerpQuotaExceeded):
            breaker.record_failure()
            raise
        except Exception as e:
            breaker.record_failure()
            logger.exception("SERP 抓取失败 %s: %s", keyword, e)
            raise

    def _scrapingbee_crawl(
        self, keyword: str, target_url: Optional[str], region: str, language: str
    ) -> SerpFetchResult:
        params = {
            "search": keyword,
            "language": language or "en",
            "pages": settings.SCRAPINGBEE_PAGES,
        }
        if region != "global":
            params["country_code"] = region
        headers = {"Authorization": f"Bearer {settings.SCRAPINGBEE_API_KEY}"}
        try:
            with httpx.Client(timeout=float(settings.SCRAPINGBEE_TIMEOUT)) as client:
                resp = client.get(SCRAPINGBEE_GOOGLE_URL, params=params, headers=headers)
        except httpx.TimeoutException as e:
            return SerpFetchResult(
                keyword=keyword,
                region=region,
                crawl_status="timeout",
                error_message=str(e),
                proxy_used="scrapingbee",
                provider="scrapingbee",
                language=language,
            )
        except httpx.HTTPError as e:
            return SerpFetchResult(
                keyword=keyword,
                region=region,
                crawl_status="error",
                error_message=str(e),
                proxy_used="scrapingbee",
                provider="scrapingbee",
                language=language,
            )

        if resp.status_code in (429, 403):
            return SerpFetchResult(
                keyword=keyword,
                region=region,
                crawl_status="blocked",
                error_message=f"ScrapingBee HTTP {resp.status_code}: {resp.text[:200]}",
                proxy_used="scrapingbee",
                provider="scrapingbee",
                language=language,
            )
        if resp.status_code >= 400:
            return SerpFetchResult(
                keyword=keyword,
                region=region,
                crawl_status="error",
                error_message=f"ScrapingBee HTTP {resp.status_code}: {resp.text[:500]}",
                proxy_used="scrapingbee",
                provider="scrapingbee",
                language=language,
            )

        try:
            payload = resp.json()
        except ValueError as e:
            return SerpFetchResult(
                keyword=keyword,
                region=region,
                crawl_status="error",
                error_message=f"ScrapingBee 响应非 JSON: {e}",
                proxy_used="scrapingbee",
                provider="scrapingbee",
                language=language,
            )

        data = payload.get("body", payload) if isinstance(payload, dict) else {}
        if not isinstance(data, dict):
            return SerpFetchResult(
                keyword=keyword,
                region=region,
                crawl_status="error",
                error_message="ScrapingBee 响应格式异常",
                proxy_used="scrapingbee",
                provider="scrapingbee",
                language=language,
            )

        organic = self._parse_scrapingbee_organic(data.get("organic_results") or [])
        target_rank, target_page = self._find_target_rank(organic, target_url)
        return SerpFetchResult(
            keyword=keyword,
            region=region,
            organic_results=organic,
            target_rank=target_rank,
            target_page=target_page,
            serp_features=self._extract_scrapingbee_features(data),
            proxy_used="scrapingbee",
            provider="scrapingbee",
            language=language,
            crawl_status="success",
            estimated_cost=float(settings.SERP_EST_COST_PER_REQUEST),
            raw_truncated=str(resp.status_code),
        )

    def _parse_scrapingbee_organic(self, raw: list[dict]) -> list[dict]:
        sorted_items = sorted(raw, key=lambda x: x.get("position", 9999))
        organic: list[dict] = []
        for rank, item in enumerate(sorted_items, start=1):
            resolved = self._resolve_scrapingbee_item(item)
            if not resolved:
                continue
            url, domain = resolved
            organic.append(
                {
                    "rank": rank,
                    "url": url,
                    "title": item.get("title") or "",
                    "domain": domain,
                }
            )
        return organic

    def _resolve_scrapingbee_item(self, item: dict) -> tuple[str, str] | None:
        url = (item.get("url") or "").strip()
        if url.startswith("http"):
            domain = item.get("domain") or self._extract_domain(url)
            return url, self._normalize_domain(domain)

        displayed = (item.get("displayed_url") or "").strip()
        if not displayed.startswith("http"):
            return None

        parts = re.split(r"\s*›\s*", displayed)
        base = parts[0].strip()
        if not base.startswith("http"):
            return None

        if len(parts) > 1:
            path = "/".join(p.strip().replace(" ", "") for p in parts[1:] if p.strip())
            url = base.rstrip("/") + ("/" + path if path else "")
        else:
            url = base

        domain = item.get("domain") or self._extract_domain(url)
        return url, self._normalize_domain(domain)

    def _extract_scrapingbee_features(self, data: dict) -> dict:
        questions = data.get("questions") or []
        ai_overviews = data.get("ai_overviews") or []
        return {
            "mock": False,
            "provider": "scrapingbee",
            "featured_snippet": bool(ai_overviews),
            "people_also_ask": len(questions),
            "related_queries": len(data.get("related_queries") or []),
            "number_of_ads": (data.get("meta_data") or {}).get("number_of_ads", 0),
        }

    def _mock_crawl(
        self, keyword: str, target_url: Optional[str], region: str, language: str
    ) -> SerpFetchResult:
        today = datetime.utcnow().strftime("%Y-%m-%d")
        seed_str = f"{keyword}|{today}|{region}"
        seed = int(hashlib.md5(seed_str.encode()).hexdigest(), 16)
        rng = random.Random(seed)

        target_rank = rng.randint(1, 100)
        target_page = (target_rank - 1) // 10 + 1

        organic: list[dict] = []
        for i in range(1, 101):
            organic.append(
                {
                    "rank": i,
                    "url": f"https://example-mock-{i}.com/{keyword.replace(' ', '-')}",
                    "title": f"{keyword} - Mock Result {i}",
                    "domain": f"example-mock-{i}.com",
                }
            )
        if target_url:
            organic[target_rank - 1]["url"] = target_url
            organic[target_rank - 1]["domain"] = self._extract_domain(target_url)

        return SerpFetchResult(
            keyword=keyword,
            region=region,
            organic_results=organic,
            target_rank=target_rank,
            target_page=target_page,
            serp_features={"mock": True, "featured_snippet": rng.random() < 0.2},
            proxy_used="mock",
            provider="mock",
            language=language,
            crawl_status="success",
            estimated_cost=0.0,
        )

    def _real_crawl(
        self, keyword: str, target_url: Optional[str], region: str, language: str
    ) -> SerpFetchResult:
        proxy = self._pool.get()
        params = {"q": keyword, "num": 100, "hl": language or "en"}
        if region != "global":
            params["gl"] = region
        headers = {
            "User-Agent": random.choice(USER_AGENTS),
            "Accept-Language": "en-US,en;q=0.9",
        }
        try:
            with httpx.Client(
                proxy=proxy.url if proxy else None,
                timeout=30.0,
                headers=headers,
                follow_redirects=True,
            ) as client:
                resp = client.get(GOOGLE_SEARCH_URL, params=params)
        except httpx.TimeoutException as e:
            if proxy:
                self._pool.mark_failed(proxy)
            return SerpFetchResult(
                keyword=keyword,
                region=region,
                crawl_status="timeout",
                error_message=str(e),
                proxy_used=proxy.url if proxy else None,
                provider="proxy",
                language=language,
            )
        except httpx.HTTPError as e:
            if proxy:
                self._pool.mark_failed(proxy)
            return SerpFetchResult(
                keyword=keyword,
                region=region,
                crawl_status="error",
                error_message=str(e),
                proxy_used=proxy.url if proxy else None,
                provider="proxy",
                language=language,
            )

        if resp.status_code == 429 or "captcha" in resp.text.lower():
            if proxy:
                self._pool.mark_failed(proxy)
            return SerpFetchResult(
                keyword=keyword,
                region=region,
                crawl_status="blocked",
                error_message="Google CAPTCHA/429",
                proxy_used=proxy.url if proxy else None,
                provider="proxy",
                language=language,
            )

        organic = self._parse_google_html(resp.text)
        target_rank, target_page = self._find_target_rank(organic, target_url)
        return SerpFetchResult(
            keyword=keyword,
            region=region,
            organic_results=organic,
            target_rank=target_rank,
            target_page=target_page,
            serp_features=self._extract_features(resp.text),
            proxy_used=proxy.url if proxy else "direct",
            provider="proxy",
            language=language,
            crawl_status="success",
        )

    def _find_target_rank(
        self, organic: list[dict], target_url: Optional[str]
    ) -> tuple[Optional[int], Optional[int]]:
        if not target_url:
            return None, None
        for r in organic:
            if r["url"].startswith(target_url) or target_url in r["url"]:
                rank = r["rank"]
                return rank, (rank - 1) // 10 + 1
        return None, None

    def _parse_google_html(self, html: str) -> list[dict]:
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html, "lxml")
        results: list[dict] = []
        for i, div in enumerate(soup.select("div.g"), start=1):
            a = div.select_one("a[href]")
            if not a:
                continue
            url = a.get("href", "")
            title = a.get_text(strip=True)
            if url.startswith("/url?q="):
                url = url.split("/url?q=")[1].split("&")[0]
            if url.startswith("http"):
                domain = re.match(r"https?://([^/]+)", url)
                results.append(
                    {
                        "rank": i,
                        "url": url,
                        "title": title,
                        "domain": domain.group(1) if domain else "",
                    }
                )
        return results

    def _extract_features(self, html: str) -> dict:
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html, "lxml")
        return {
            "mock": False,
            "provider": "proxy",
            "featured_snippet": bool(
                soup.select_one("div.g.xpdopen, div[data-featured='1']")
            ),
            "people_also_ask": len(soup.select("div.related-question-pair")),
            "knowledge_panel": bool(soup.select_one("div.kp-blk")),
        }

    def _match_competitors(self, organic: list[dict], domains: list[str]) -> dict:
        matched: dict[str, dict] = {}
        normalized = [self._normalize_domain(d) for d in domains]
        for r in organic:
            result_domain = self._normalize_domain(r["domain"])
            for raw, norm in zip(domains, normalized):
                if norm and norm in result_domain:
                    matched[raw] = {"rank": r["rank"], "url": r["url"]}
                    break
        return matched

    def _normalize_domain(self, value: str) -> str:
        value = value.strip().lower()
        value = re.sub(r"^https?://", "", value)
        value = value.split("/")[0]
        if value.startswith("www."):
            value = value[4:]
        return value

    def _extract_domain(self, url: str) -> str:
        m = re.match(r"https?://([^/]+)", url)
        return m.group(1) if m else ""


_crawler_singleton: SerpCrawler | None = None


def get_serp_crawler() -> SerpCrawler:
    global _crawler_singleton
    if _crawler_singleton is None:
        _crawler_singleton = SerpCrawler()
    return _crawler_singleton


def get_serp_provider() -> SERPProvider:
    """业务侧统一取 Provider，便于日后替换实现。"""
    return get_serp_crawler()


def reset_serp_crawler() -> None:
    """测试用：清空单例以便切换配置。"""
    global _crawler_singleton
    _crawler_singleton = None
