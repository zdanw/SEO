"""One-off ScrapingBee integration test."""
import json
import sys

import httpx

from app.core.config import settings
from app.services.serp_crawler import SerpCrawler

OUT = "scripts/scrapingbee_test_result.json"


def main() -> int:
    print(f"SCRAPINGBEE_API_KEY set: {bool(settings.SCRAPINGBEE_API_KEY)}")
    crawler = SerpCrawler()
    print(f"mode: scrapingbee={crawler.is_scrapingbee} mock={crawler.is_mock}")

    # Raw API probe
    resp = httpx.get(
        "https://app.scrapingbee.com/api/v1/google",
        params={
            "search": "python seo tools",
            "country_code": "us",
            "language": "en",
            "pages": 1,
        },
        headers={"Authorization": f"Bearer {settings.SCRAPINGBEE_API_KEY}"},
        timeout=float(settings.SCRAPINGBEE_TIMEOUT),
    )
    print(f"raw HTTP: {resp.status_code}")
    raw = resp.json()
    body = raw.get("body", raw) if isinstance(raw, dict) else raw
    organic = body.get("organic_results", []) if isinstance(body, dict) else []
    print(f"raw organic_results count: {len(organic)}")

    summary = {
        "http_status": resp.status_code,
        "top_level_keys": list(raw.keys()) if isinstance(raw, dict) else [],
        "body_keys": list(body.keys()) if isinstance(body, dict) else [],
        "organic_count": len(organic),
        "first_organic": organic[:2] if organic else None,
        "meta_data": body.get("meta_data") if isinstance(body, dict) else None,
    }
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump({"summary": summary, "raw_sample": raw}, f, ensure_ascii=False, indent=2)
    print(f"saved: {OUT}")

    # Crawler integration test
    result = crawler.crawl("python seo tools", "https://www.python.org", "us")
    crawl_summary = {
        "crawl_status": result.crawl_status,
        "error_message": result.error_message,
        "proxy_used": result.proxy_used,
        "target_rank": result.target_rank,
        "organic_count": len(result.organic_results),
        "top3": [
            {"rank": r["rank"], "domain": r["domain"], "url": r["url"]}
            for r in result.organic_results[:3]
        ],
        "serp_features": result.serp_features,
    }
    print("crawler:", json.dumps(crawl_summary, ensure_ascii=False, indent=2))

    if result.crawl_status != "success":
        return 1
    if len(result.organic_results) == 0:
        print("WARN: success but zero organic results - check response format")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
