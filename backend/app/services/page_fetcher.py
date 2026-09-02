"""抓取外部页面 HTML 并提取 SEO 相关字段。"""
from __future__ import annotations

import re
from html import unescape
from urllib.parse import urljoin, urlparse

import httpx


def fetch_page_content(url: str, timeout: float = 20.0) -> dict[str, str]:
    """抓取 URL 并提取 title / meta / 正文文本。"""
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (compatible; SEOPlatformBot/1.0; +https://seo-platform.local)"
        ),
    }
    with httpx.Client(timeout=timeout, follow_redirects=True, headers=headers) as client:
        resp = client.get(url)
        resp.raise_for_status()
        html = resp.text

    title = _extract_tag(html, "title") or ""
    meta_desc = _extract_meta(html, "description") or ""
    h1 = _extract_tag(html, "h1") or ""
    body_text = _strip_html(html)
    cover = _extract_og_image(html, url)

    content_parts = []
    if h1:
        content_parts.append(f"# {h1}")
    content_parts.append(body_text[:8000])

    return {
        "title": unescape(title.strip())[:300],
        "meta_description": unescape(meta_desc.strip())[:320],
        "content": "\n\n".join(content_parts),
        "cover_image_url": cover or "",
        "source_url": str(resp.url),
    }


def discover_sitemap_urls(sitemap_url: str, max_urls: int = 200) -> list[str]:
    """从 sitemap.xml 解析 URL 列表。"""
    headers = {"User-Agent": "Mozilla/5.0 (compatible; SEOPlatformBot/1.0)"}
    urls: list[str] = []
    with httpx.Client(timeout=20.0, follow_redirects=True, headers=headers) as client:
        resp = client.get(sitemap_url)
        resp.raise_for_status()
        text = resp.text
        locs = re.findall(r"<loc>\s*(.*?)\s*</loc>", text, re.IGNORECASE)
        for loc in locs:
            loc = loc.strip()
            if loc.endswith(".xml") and "sitemap" in loc.lower() and len(urls) < max_urls:
                try:
                    sub = discover_sitemap_urls(loc, max_urls=max_urls - len(urls))
                    urls.extend(sub)
                except Exception:
                    continue
            elif loc.startswith("http"):
                urls.append(loc)
            if len(urls) >= max_urls:
                break
    return urls[:max_urls]


def _extract_tag(html: str, tag: str) -> str | None:
    m = re.search(rf"<{tag}[^>]*>(.*?)</{tag}>", html, re.IGNORECASE | re.DOTALL)
    return m.group(1).strip() if m else None


def _extract_meta(html: str, name: str) -> str | None:
    m = re.search(
        rf'<meta[^>]+name=["\']{name}["\'][^>]+content=["\'](.*?)["\']',
        html,
        re.IGNORECASE,
    )
    if not m:
        m = re.search(
            rf'<meta[^>]+content=["\'](.*?)["\'][^>]+name=["\']{name}["\']',
            html,
            re.IGNORECASE,
        )
    return m.group(1) if m else None


def _extract_og_image(html: str, base_url: str) -> str | None:
    m = re.search(
        r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\'](.*?)["\']',
        html,
        re.IGNORECASE,
    )
    if m:
        return urljoin(base_url, m.group(1))
    return None


def _strip_html(html: str) -> str:
    text = re.sub(r"<(script|style|noscript)[^>]*>.*?</\1>", " ", html, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"<[^>]+>", " ", text)
    text = unescape(re.sub(r"\s+", " ", text))
    return text.strip()


def url_belongs_to_domain(url: str, domain: str) -> bool:
    """检查 URL 是否属于指定域名。"""
    try:
        host = urlparse(url).netloc.lower().removeprefix("www.")
        target = domain.lower().removeprefix("www.")
        return host == target or host.endswith("." + target)
    except Exception:
        return False
