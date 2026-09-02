"""WordPress / 通用 CMS 内容同步。"""
from __future__ import annotations

import base64
import logging
from typing import Any

import httpx

from app.models.article import Article
from app.models.client_site import ClientSite

logger = logging.getLogger(__name__)


class CmsSyncError(Exception):
    pass


def sync_article_to_cms(site: ClientSite, article: Article) -> dict[str, Any]:
    """将文章推送到客户 CMS，返回同步结果。"""
    if site.cms_type == "wordpress":
        return _sync_wordpress(site, article)
    if site.cms_type == "none":
        raise CmsSyncError("客户站点未配置 CMS 类型")
    raise CmsSyncError(f"暂不支持的 CMS 类型: {site.cms_type}")


def _sync_wordpress(site: ClientSite, article: Article) -> dict[str, Any]:
    if not site.cms_api_url or not site.cms_api_key:
        raise CmsSyncError("请配置 WordPress API URL 和 Application Password")

    api_base = site.cms_api_url.rstrip("/")
    if not api_base.endswith("/wp-json/wp/v2"):
        api_base = f"{api_base}/wp-json/wp/v2"

    payload: dict[str, Any] = {
        "title": article.title,
        "content": article.content or "",
        "status": "publish" if article.status == "published" else "draft",
        "excerpt": article.meta_description or "",
    }
    if article.slug:
        payload["slug"] = article.slug

    headers = {"Content-Type": "application/json"}
    if ":" in site.cms_api_key:
        user, password = site.cms_api_key.split(":", 1)
        token = base64.b64encode(f"{user}:{password}".encode()).decode()
        headers["Authorization"] = f"Basic {token}"
    else:
        headers["Authorization"] = f"Bearer {site.cms_api_key}"

    with httpx.Client(timeout=30.0) as client:
        if article.cms_post_id:
            resp = client.post(f"{api_base}/posts/{article.cms_post_id}", json=payload, headers=headers)
        else:
            resp = client.post(f"{api_base}/posts", json=payload, headers=headers)

    if resp.status_code >= 400:
        raise CmsSyncError(f"WordPress API 错误 ({resp.status_code}): {resp.text[:300]}")

    data = resp.json()
    post_id = str(data.get("id", ""))
    link = data.get("link", "")
    return {"cms_post_id": post_id, "source_url": link, "sync_status": "synced"}
