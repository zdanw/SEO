"""基于 ScrapingBee Google API 的真实 SERP 竞品基准分析。"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import urlparse

import httpx

from app.core.config import settings
from app.services.page_fetcher import fetch_page_content
from app.services.serp_crawler import SerpCrawler, SerpCrawlError, get_serp_crawler

logger = logging.getLogger(__name__)

SCRAPINGBEE_SCRAPE_URL = "https://app.scrapingbee.com/api/v1"

# 简单内存缓存，避免重复消耗 ScrapingBee 额度
_CACHE: dict[str, tuple[datetime, dict[str, Any]]] = {}
_CACHE_TTL = timedelta(hours=1)

_SKIP_DOMAINS = frozenset({
    "youtube.com", "www.youtube.com",
    "amazon.com", "www.amazon.com",
    "facebook.com", "www.facebook.com",
    "instagram.com", "www.instagram.com",
    "twitter.com", "x.com", "www.twitter.com",
    "pinterest.com", "www.pinterest.com",
    "wikipedia.org", "en.wikipedia.org",
})


def fetch_serp_benchmark(keyword: str, region: str | None = None) -> dict[str, Any] | None:
    """抓取真实 SERP 并分析 Top N 竞品页面。失败返回 None（由调用方回退模拟）。"""
    if not settings.SCRAPINGBEE_API_KEY:
        return None

    region = region or settings.SERP_BENCHMARK_REGION
    cache_key = f"{keyword.lower()}|{region}"
    cached = _CACHE.get(cache_key)
    if cached and datetime.utcnow() - cached[0] < _CACHE_TTL:
        return cached[1]

    crawler = get_serp_crawler()
    if not crawler.is_scrapingbee:
        return None

    try:
        serp = crawler.crawl(keyword, target_url=None, region=region)
    except SerpCrawlError as exc:
        logger.warning("SERP 抓取失败 %s: %s", keyword, exc)
        return None

    if serp.crawl_status != "success" or not serp.organic_results:
        logger.warning(
            "SERP 无结果 keyword=%s status=%s err=%s",
            keyword, serp.crawl_status, serp.error_message,
        )
        return None

    competitors, skipped = _select_competitor_candidates(serp.organic_results)
    analyzed: list[dict[str, Any]] = []
    all_headings: list[str] = []
    seen_urls: set[str] = set()

    for item in competitors:
        if len(analyzed) >= settings.SERP_BENCHMARK_TOP_N:
            break
        url = item["url"]
        norm = _normalize_url(url)
        if norm in seen_urls:
            skipped.append({
                "google_rank": item["rank"],
                "title": item.get("title", ""),
                "url": url,
                "domain": item.get("domain", ""),
                "reason": "重复 URL，已跳过",
            })
            continue

        page = _analyze_competitor_page(url, item.get("title", ""))
        if not page:
            skipped.append({
                "google_rank": item["rank"],
                "title": item.get("title", ""),
                "url": url,
                "domain": item.get("domain", ""),
                "reason": "页面抓取失败",
            })
            continue

        seen_urls.add(norm)
        analyzed.append({
            "googleRank": item["rank"],
            "position": item["rank"],
            "url": url,
            "domain": item.get("domain", ""),
            "title": page.get("title") or item.get("title", ""),
            "wordCount": page["word_count"],
            "headingCount": page["heading_count"],
            "h2Count": page["h2_count"],
            "h3Count": page["h3_count"],
        })
        all_headings.extend(page.get("headings", []))

    if not analyzed:
        return None

    word_counts = [p["wordCount"] for p in analyzed if p["wordCount"] > 0]
    heading_counts = [p["headingCount"] for p in analyzed]
    avg_wc = round(sum(word_counts) / len(word_counts)) if word_counts else 1500
    avg_h = round(sum(heading_counts) / len(heading_counts)) if heading_counts else 5

    common_topics = _extract_common_topics(all_headings, serp.serp_features, keyword)

    benchmark = {
        "keyword": keyword,
        "dataSource": "scrapingbee",
        "region": region,
        "topResults": analyzed,
        "averages": {
            "wordCount": avg_wc,
            "headingCount": avg_h,
            "recommendedWordCount": round(avg_wc * 1.1),
        },
        "commonTopics": common_topics,
        "serpFeatures": {
            "featuredSnippet": bool(serp.serp_features.get("featured_snippet")),
            "peopleAlsoAsk": int(serp.serp_features.get("people_also_ask") or 0) > 0,
            "videoResults": False,
            "relatedQueries": serp.serp_features.get("related_queries", 0),
            "provider": serp.serp_features.get("provider", "scrapingbee"),
        },
        "organicCount": len(serp.organic_results),
        "pagesAnalyzed": len(analyzed),
        "skippedResults": skipped[:10],
    }
    _CACHE[cache_key] = (datetime.utcnow(), benchmark)
    return benchmark


def _select_competitor_candidates(organic: list[dict]) -> tuple[list[dict], list[dict]]:
    """从 SERP 结果中筛选可分析候选（跳过电商/视频等），并记录跳过原因。"""
    selected: list[dict] = []
    skipped: list[dict] = []
    # 扫描更多结果，确保剔除跳过后仍能凑够分析数量
    scan_limit = max(settings.SERP_BENCHMARK_TOP_N * 4, 20)

    for item in organic[:scan_limit]:
        url = (item.get("url") or "").strip()
        if not url.startswith("http"):
            continue
        domain = _normalize_host(url)
        if domain in _SKIP_DOMAINS:
            skipped.append({
                "google_rank": item.get("rank"),
                "title": item.get("title", ""),
                "url": url,
                "domain": domain,
                "reason": "电商/视频/社交平台，不参与内容分析",
            })
            continue
        selected.append(item)
    return selected, skipped


def _normalize_url(url: str) -> str:
    """URL 去重键（去掉 fragment 与尾部斜杠）。"""
    try:
        p = urlparse(url.strip())
        path = p.path.rstrip("/") or "/"
        return f"{p.scheme}://{p.netloc.lower()}{path}"
    except Exception:
        return url.strip().lower()


def _analyze_competitor_page(url: str, fallback_title: str) -> dict[str, Any] | None:
    """抓取竞品页并统计字数、标题层级。"""
    try:
        page = fetch_page_content(url, timeout=15.0)
    except Exception:
        html = _fetch_html_via_scrapingbee(url)
        if not html:
            return None
        page = _parse_html_page(html, url)

    content = page.get("content") or ""
    headings = _extract_headings_from_content(content)
    word_count = _count_words(content)
    h2_count = sum(1 for h in headings if h["level"] == 2)
    h3_count = sum(1 for h in headings if h["level"] == 3)

    return {
        "title": page.get("title") or fallback_title,
        "word_count": word_count,
        "heading_count": len(headings),
        "h2_count": h2_count,
        "h3_count": h3_count,
        "headings": [h["text"] for h in headings if h["level"] in (2, 3)],
    }


def _fetch_html_via_scrapingbee(url: str) -> str | None:
    """ScrapingBee 通用抓取 API（竞品页直连失败时使用）。"""
    if not settings.SCRAPINGBEE_API_KEY:
        return None
    params = {
        "api_key": settings.SCRAPINGBEE_API_KEY,
        "url": url,
        "render_js": "false",
    }
    try:
        with httpx.Client(timeout=float(settings.SCRAPINGBEE_TIMEOUT)) as client:
            resp = client.get(SCRAPINGBEE_SCRAPE_URL, params=params)
        if resp.status_code >= 400:
            return None
        return resp.text
    except httpx.HTTPError:
        return None


def _parse_html_page(html: str, url: str) -> dict[str, str]:
    """从 HTML 提取基础字段（与 page_fetcher 逻辑一致）。"""
    from html import unescape

    title_m = re.search(r"<title[^>]*>(.*?)</title>", html, re.I | re.S)
    title = unescape(title_m.group(1).strip()) if title_m else ""
    h1_m = re.search(r"<h1[^>]*>(.*?)</h1>", html, re.I | re.S)
    h1 = re.sub(r"<[^>]+>", "", h1_m.group(1)).strip() if h1_m else ""

    parts = [f"# {h1}"] if h1 else []
    body = re.sub(r"<(script|style|noscript)[^>]*>.*?</\1>", " ", html, flags=re.I | re.S)
    for level in range(2, 4):
        for m in re.finditer(rf"<h{level}[^>]*>(.*?)</h{level}>", body, re.I | re.S):
            text = re.sub(r"<[^>]+>", "", m.group(1)).strip()
            if text:
                parts.append("#" * level + " " + text)
    text = re.sub(r"<[^>]+>", " ", body)
    text = unescape(re.sub(r"\s+", " ", text)).strip()
    parts.append(text[:8000])

    return {"title": title[:300], "content": "\n\n".join(parts), "source_url": url}


def _extract_headings_from_content(content: str) -> list[dict[str, int | str]]:
    headings: list[dict[str, int | str]] = []
    for line in content.split("\n"):
        m = re.match(r"^(#{1,6})\s+(.+)", line.strip())
        if m:
            headings.append({"level": len(m.group(1)), "text": m.group(2).strip()})
    return headings


def _extract_common_topics(
    headings: list[str],
    serp_features: dict,
    keyword: str,
) -> list[str]:
    """从竞品 H2/H3 与 SERP 特征提取话题列表。"""
    topics: list[str] = []
    seen: set[str] = set()

    def add(topic: str) -> None:
        t = topic.strip()
        if len(t) < 3 or len(t) > 80:
            return
        key = t.lower()
        if key not in seen:
            seen.add(key)
            topics.append(t)

    for h in headings:
        # 去掉过长的标题，保留核心短语
        clean = re.sub(r"\s+", " ", h).strip()
        if 3 <= len(clean) <= 60:
            add(clean)

    # People Also Ask 数量提示 — 具体问句需从 raw API 扩展，此处用关键词变体
    kw = keyword.strip()
    if kw:
        add(f"{kw} benefits")
        add(f"{kw} review")
        add(f"how to choose {kw}")
        add(f"{kw} comparison")

    return topics[:20]


def _count_words(text: str) -> int:
    return len(re.findall(r"\S+", text))


def _normalize_host(url: str) -> str:
    try:
        host = urlparse(url).netloc.lower()
        return host[4:] if host.startswith("www.") else host
    except Exception:
        return ""
