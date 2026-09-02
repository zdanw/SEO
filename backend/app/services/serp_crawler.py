"""Google SERP 爬虫服务。

模式（优先级从高到低）：
1. ScrapingBee 模式（SCRAPINGBEE_API_KEY）：调用 Google Search API，返回结构化 JSON。
2. 代理模式（PROXY_LIST / PROXY_ENDPOINT）：httpx + 代理访问 Google，BeautifulSoup 解析。
3. Mock 模式（以上均未配置）：生成随机排名 1-100，同关键词同日结果稳定。

竞品抓取（P3.7）：在抓取 SERP 时同时遍历结果列表，匹配用户配置的竞品域名。
"""
from __future__ import annotations

import hashlib
import logging
import random
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

import httpx

from app.core.config import settings
from app.utils.proxy import get_proxy_pool, ProxyInfo
from app.utils.rate_limiter import acquire_token, CircuitBreaker, CircuitBreakerOpen, RateLimitExceeded

logger = logging.getLogger(__name__)

GOOGLE_SEARCH_URL = "https://www.google.com/search"
SCRAPINGBEE_GOOGLE_URL = "https://app.scrapingbee.com/api/v1/google"
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:131.0) Gecko/20100101 Firefox/131.0",
]


class SerpCrawlError(RuntimeError):
    """SERP 抓取基础错误。"""


class SerpBlockedError(SerpCrawlError):
    """触发验证码/IP 被封。"""


class SerpTimeoutError(SerpCrawlError):
    """请求超时。"""


@dataclass
class SerpResult:
    """单次 SERP 抓取结果。"""
    keyword: str
    region: str
    organic_results: list[dict] = field(default_factory=list)
    target_rank: Optional[int] = None
    target_page: Optional[int] = None
    serp_features: dict = field(default_factory=dict)
    crawl_status: str = "success"
    error_message: Optional[str] = None
    proxy_used: Optional[str] = None
    fetched_at: datetime = field(default_factory=datetime.utcnow)


def _has_proxy_config() -> bool:
    return bool(settings.PROXY_LIST or settings.PROXY_ENDPOINT)


class SerpCrawler:
    """SERP 爬虫主类。"""

    def __init__(self) -> None:
        self._scrapingbee_mode = bool(settings.SCRAPINGBEE_API_KEY)
        self._mock_mode = not self._scrapingbee_mode and not _has_proxy_config()
        self._pool = get_proxy_pool()

    @property
    def is_mock(self) -> bool:
        return self._mock_mode

    @property
    def is_scrapingbee(self) -> bool:
        return self._scrapingbee_mode

    def crawl(
        self,
        keyword: str,
        target_url: Optional[str],
        region: str = "us",
        competitor_domains: list[str] | None = None,
    ) -> SerpResult:
        """抓取单关键词 SERP。"""
        if not acquire_token("serp", keyword):
            raise RateLimitExceeded(f"SERP 抓取 {keyword} 被限流")
        breaker = CircuitBreaker("serp", keyword)
        if not breaker.allow_request():
            raise CircuitBreakerOpen(f"SERP 抓取 {keyword} 熔断器打开")
        try:
            if self._scrapingbee_mode:
                result = self._scrapingbee_crawl(keyword, target_url, region)
            elif self._mock_mode:
                result = self._mock_crawl(keyword, target_url, region)
            else:
                result = self._real_crawl(keyword, target_url, region)
            breaker.record_success()
            if competitor_domains and result.organic_results:
                result.serp_features["competitor_ranks"] = self._match_competitors(
                    result.organic_results, competitor_domains
                )
            return result
        except (SerpBlockedError, SerpTimeoutError):
            breaker.record_failure()
            raise
        except Exception as e:
            breaker.record_failure()
            logger.exception("SERP 抓取失败 %s: %s", keyword, e)
            raise

    def _scrapingbee_crawl(
        self, keyword: str, target_url: Optional[str], region: str
    ) -> SerpResult:
        """ScrapingBee Google Search API 模式。"""
        params = {
            "search": keyword,
            "language": "en",
            "pages": settings.SCRAPINGBEE_PAGES,
        }
        if region != "global":
            params["country_code"] = region
        headers = {"Authorization": f"Bearer {settings.SCRAPINGBEE_API_KEY}"}
        try:
            with httpx.Client(timeout=float(settings.SCRAPINGBEE_TIMEOUT)) as client:
                resp = client.get(SCRAPINGBEE_GOOGLE_URL, params=params, headers=headers)
        except httpx.TimeoutException as e:
            return SerpResult(
                keyword=keyword,
                region=region,
                crawl_status="timeout",
                error_message=str(e),
                proxy_used="scrapingbee",
            )
        except httpx.HTTPError as e:
            return SerpResult(
                keyword=keyword,
                region=region,
                crawl_status="error",
                error_message=str(e),
                proxy_used="scrapingbee",
            )

        if resp.status_code in (429, 403):
            return SerpResult(
                keyword=keyword,
                region=region,
                crawl_status="blocked",
                error_message=f"ScrapingBee HTTP {resp.status_code}: {resp.text[:200]}",
                proxy_used="scrapingbee",
            )
        if resp.status_code >= 400:
            return SerpResult(
                keyword=keyword,
                region=region,
                crawl_status="error",
                error_message=f"ScrapingBee HTTP {resp.status_code}: {resp.text[:500]}",
                proxy_used="scrapingbee",
            )

        try:
            payload = resp.json()
        except ValueError as e:
            return SerpResult(
                keyword=keyword,
                region=region,
                crawl_status="error",
                error_message=f"ScrapingBee 响应非 JSON: {e}",
                proxy_used="scrapingbee",
            )

        data = payload.get("body", payload) if isinstance(payload, dict) else {}
        if not isinstance(data, dict):
            return SerpResult(
                keyword=keyword,
                region=region,
                crawl_status="error",
                error_message="ScrapingBee 响应格式异常",
                proxy_used="scrapingbee",
            )

        organic = self._parse_scrapingbee_organic(data.get("organic_results") or [])
        target_rank, target_page = self._find_target_rank(organic, target_url)

        return SerpResult(
            keyword=keyword,
            region=region,
            organic_results=organic,
            target_rank=target_rank,
            target_page=target_page,
            serp_features=self._extract_scrapingbee_features(data),
            proxy_used="scrapingbee",
            crawl_status="success",
        )

    def _parse_scrapingbee_organic(self, raw: list[dict]) -> list[dict]:
        """将 ScrapingBee organic_results 转为统一格式。"""
        sorted_items = sorted(raw, key=lambda x: x.get("position", 9999))
        organic: list[dict] = []
        for rank, item in enumerate(sorted_items, start=1):
            resolved = self._resolve_scrapingbee_item(item)
            if not resolved:
                continue
            url, domain = resolved
            organic.append({
                "rank": rank,
                "url": url,
                "title": item.get("title") or "",
                "domain": domain,
            })
        return organic

    def _resolve_scrapingbee_item(self, item: dict) -> tuple[str, str] | None:
        """解析 ScrapingBee 结果项 URL（可能是 /goto 重定向或 displayed_url）。"""
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

    def _mock_crawl(self, keyword: str, target_url: Optional[str], region: str) -> SerpResult:
        """Mock 模式：随机生成排名，同关键词同日结果稳定。"""
        today = datetime.utcnow().strftime("%Y-%m-%d")
        seed_str = f"{keyword}|{today}|{region}"
        seed = int(hashlib.md5(seed_str.encode()).hexdigest(), 16)
        rng = random.Random(seed)

        target_rank = rng.randint(1, 100)
        target_page = (target_rank - 1) // 10 + 1

        organic: list[dict] = []
        for i in range(1, 101):
            organic.append({
                "rank": i,
                "url": f"https://example-mock-{i}.com/{keyword.replace(' ', '-')}",
                "title": f"{keyword} - Mock Result {i}",
                "domain": f"example-mock-{i}.com",
            })
        if target_url:
            organic[target_rank - 1]["url"] = target_url
            organic[target_rank - 1]["domain"] = self._extract_domain(target_url)

        return SerpResult(
            keyword=keyword,
            region=region,
            organic_results=organic,
            target_rank=target_rank,
            target_page=target_page,
            serp_features={"mock": True, "featured_snippet": rng.random() < 0.2},
            proxy_used="mock",
            crawl_status="success",
        )

    def _real_crawl(self, keyword: str, target_url: Optional[str], region: str) -> SerpResult:
        """真实模式：httpx + 代理访问 Google。"""
        proxy = self._pool.get()
        params = {"q": keyword, "num": 100, "hl": "en"}
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
            return SerpResult(
                keyword=keyword, region=region,
                crawl_status="timeout", error_message=str(e),
                proxy_used=proxy.url if proxy else None,
            )
        except httpx.HTTPError as e:
            if proxy:
                self._pool.mark_failed(proxy)
            return SerpResult(
                keyword=keyword, region=region,
                crawl_status="error", error_message=str(e),
                proxy_used=proxy.url if proxy else None,
            )

        if resp.status_code == 429 or "captcha" in resp.text.lower():
            if proxy:
                self._pool.mark_failed(proxy)
            return SerpResult(
                keyword=keyword, region=region,
                crawl_status="blocked", error_message="Google CAPTCHA/429",
                proxy_used=proxy.url if proxy else None,
            )

        organic = self._parse_google_html(resp.text)
        target_rank, target_page = self._find_target_rank(organic, target_url)

        return SerpResult(
            keyword=keyword, region=region,
            organic_results=organic,
            target_rank=target_rank, target_page=target_page,
            serp_features=self._extract_features(resp.text),
            proxy_used=proxy.url if proxy else "direct",
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
        """BeautifulSoup 解析 Google 结果页 organic 结果。"""
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
                results.append({
                    "rank": i, "url": url, "title": title,
                    "domain": domain.group(1) if domain else "",
                })
        return results

    def _extract_features(self, html: str) -> dict:
        """提取 SERP Features。"""
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "lxml")
        return {
            "mock": False,
            "featured_snippet": bool(soup.select_one("div.g.xpdopen, div[data-featured='1']")),
            "people_also_ask": len(soup.select("div.related-question-pair")),
            "knowledge_panel": bool(soup.select_one("div.kp-blk")),
        }

    def _match_competitors(self, organic: list[dict], domains: list[str]) -> dict:
        """从 organic 结果中匹配竞品域名。"""
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
        """去掉协议和 www 前缀，便于域名匹配。"""
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
