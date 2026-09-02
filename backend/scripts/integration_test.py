"""Multi-site integration test script."""
from __future__ import annotations

import sys
import time
from typing import Any

import httpx

BASE = "http://127.0.0.1:8000/api/v1"
TEST_EMAIL = "integration-test@example.com"
TEST_PASSWORD = "TestPass123!"
TEST_DOMAIN = "example.com"
TEST_SITE_MARKER = "Integration Test Client"

passed = 0
failed = 0


def ok(name: str, detail: str = "") -> None:
    global passed
    passed += 1
    print(f"  [PASS] {name}" + (f" - {detail}" if detail else ""))


def fail(name: str, detail: str) -> None:
    global failed
    failed += 1
    print(f"  [FAIL] {name} - {detail}")


def step(name: str, fn) -> Any:
    print(f"\n>> {name}")
    try:
        return fn()
    except Exception as exc:
        fail(name, str(exc))
        return None


def main() -> int:
    client = httpx.Client(timeout=120.0, base_url=BASE)
    token: str | None = None
    site_id: int | None = None
    keyword_id: int | None = None
    article_id: int | None = None
    headers: dict[str, str] = {}

    step("Health", lambda: _health(client))

    def login_flow():
        nonlocal token
        r = client.post("/auth/register", json={
            "email": TEST_EMAIL,
            "password": TEST_PASSWORD,
            "full_name": "Integration Test",
        })
        if r.status_code not in (200, 400):
            raise RuntimeError(f"register {r.status_code}: {r.text[:200]}")
        r = client.post("/auth/login", json={"email": TEST_EMAIL, "password": TEST_PASSWORD})
        if r.status_code != 200:
            raise RuntimeError(f"login {r.status_code}: {r.text[:200]}")
        token = r.json()["access_token"]
        headers["Authorization"] = f"Bearer {token}"
        ok("Login", f"token prefix={token[:16]}")

    step("Auth", login_flow)
    if not token:
        return 1

    def sites_flow():
        nonlocal site_id
        r = client.get("/sites", headers=headers)
        if r.status_code != 200:
            raise RuntimeError(f"list sites {r.status_code}: {r.text[:200]}")
        sites = r.json()
        ok("List sites", f"count={len(sites)}")

        test_site = next(
            (s for s in sites if s["domain"] == TEST_DOMAIN and TEST_SITE_MARKER in s["name"]),
            None,
        )
        if not test_site:
            r = client.post("/sites", headers=headers, json={
                "name": TEST_SITE_MARKER,
                "domain": TEST_DOMAIN,
                "cms_type": "none",
                "sitemap_url": f"https://{TEST_DOMAIN}/sitemap.xml",
                "industry": "testing",
            })
            if r.status_code != 201:
                raise RuntimeError(f"create site {r.status_code}: {r.text[:200]}")
            test_site = r.json()
            ok("Create site", f"id={test_site['id']}")
        else:
            ok("Reuse site", f"id={test_site['id']}")

        site_id = test_site["id"]
        headers["X-Site-Id"] = str(site_id)

    step("Client sites", sites_flow)
    if not site_id:
        return 1

    def keywords_flow():
        nonlocal keyword_id
        r = client.post("/keywords", headers=headers, json={
            "keyword": "integration test seo keyword",
            "target_url": f"https://{TEST_DOMAIN}/test-page",
            "priority": 1,
        })
        if r.status_code != 201:
            raise RuntimeError(f"create keyword {r.status_code}: {r.text[:200]}")
        keyword_id = r.json()["id"]
        ok("Create keyword", f"id={keyword_id}")
        r = client.get("/keywords", headers=headers)
        if r.status_code != 200:
            raise RuntimeError("list keywords failed")
        ok("List keywords", f"count={len(r.json())}")

    step("Keywords", keywords_flow)

    def articles_flow():
        nonlocal article_id
        r = client.post("/articles", headers=headers, json={
            "title": "Integration SEO Article Title",
            "meta_description": "Integration test meta description for SEO platform validation workflow.",
            "content": "# Integration Test\n\nintegration test seo keyword appears here.\n\n## Section\n\nMore content.",
            "keyword_id": keyword_id,
        })
        if r.status_code != 201:
            raise RuntimeError(f"create article {r.status_code}: {r.text[:200]}")
        article_id = r.json()["id"]
        ok("Create article", f"id={article_id}")
        r = client.post(f"/articles/{article_id}/status", headers=headers, json={"status": "reviewed"})
        if r.status_code != 200:
            raise RuntimeError(f"reviewed {r.status_code}: {r.text[:200]}")
        r = client.post(f"/articles/{article_id}/status", headers=headers, json={"status": "published"})
        if r.status_code != 200:
            raise RuntimeError(f"published {r.status_code}: {r.text[:200]}")
        ok("Article status", "draft->reviewed->published")

    step("Articles", articles_flow)

    def seo_flow():
        r = client.post("/seo/check", json={
            "title": "Integration SEO Test Title",
            "content": "# Hello\n\nintegration test seo keyword content.",
            "meta_description": "Meta for integration test.",
            "keyword": "integration test seo keyword",
        })
        if r.status_code != 200:
            raise RuntimeError(f"seo check {r.status_code}: {r.text[:200]}")
        ok("SEO check", f"score={r.json().get('total_score')}")
        if article_id:
            r = client.post(f"/seo/check/{article_id}", headers=headers, json={"keyword": "integration test"})
            if r.status_code != 200:
                raise RuntimeError(f"seo save {r.status_code}: {r.text[:200]}")
            ok("SEO save to article", f"score={r.json().get('total_score')}")

    step("SEO checker", seo_flow)

    def monitor_flow():
        r = client.post("/competitors", headers=headers, json={
            "domain": "competitor-example.com",
            "name": "Integration Competitor",
        })
        if r.status_code not in (201, 400):
            raise RuntimeError(f"competitor {r.status_code}: {r.text[:200]}")
        ok("Competitor", r.json().get("domain", "already exists"))
        r = client.post("/backlinks", headers=headers, json={
            "target_url": f"https://{TEST_DOMAIN}/test-page",
            "source_url": "https://referrer-example.com/link",
            "anchor_text": "integration test",
        })
        if r.status_code not in (201, 400):
            raise RuntimeError(f"backlink {r.status_code}: {r.text[:200]}")
        ok("Backlink", r.json().get("id", "already exists") if r.status_code == 201 else "already exists")

    step("Competitors & backlinks", monitor_flow)

    def serp_flow():
        r = client.get("/serp/config", headers=headers)
        if r.status_code != 200:
            raise RuntimeError(f"serp config {r.status_code}")
        ok("SERP config", f"mode={r.json().get('mode')}")
        if keyword_id:
            r = client.post(f"/serp/crawl/{keyword_id}", headers=headers)
            if r.status_code != 200:
                raise RuntimeError(f"serp crawl {r.status_code}: {r.text[:200]}")
            ok("SERP crawl trigger", str(r.json().get("task_id", ""))[:24])
            time.sleep(4)
            r = client.get("/serp/latest", headers=headers)
            if r.status_code != 200:
                raise RuntimeError(f"serp latest {r.status_code}")
            ok("SERP latest", f"keywords={len(r.json())}")

    step("SERP monitor", serp_flow)

    def dashboard_flow():
        for ep in ("summary", "rank-trends", "social-funnel", "backlink-stats"):
            r = client.get(f"/dashboard/{ep}", headers=headers, params={"days": 7})
            if r.status_code != 200:
                raise RuntimeError(f"dashboard/{ep} {r.status_code}: {r.text[:200]}")
        ok("Dashboard APIs", "4 endpoints OK")
        r = client.post("/dashboard/recommendations/generate", headers=headers, params={"days_lookback": 7})
        if r.status_code != 200:
            raise RuntimeError(f"recommender {r.status_code}: {r.text[:200]}")
        ok("Recommender", f"created={r.json().get('created', 0)}")
        r = client.get("/dashboard/recommendations", headers=headers)
        if r.status_code != 200:
            raise RuntimeError(f"recommendations {r.status_code}")
        ok("Recommendations", f"total={r.json().get('total', 0)}")

    step("Dashboard", dashboard_flow)

    def gsc_flow():
        r = client.get("/gsc/status", headers=headers)
        if r.status_code != 200:
            raise RuntimeError(f"gsc {r.status_code}: {r.text[:200]}")
        d = r.json()
        ok("GSC status", f"configured={d.get('configured')} connected={d.get('connected')}")

    step("Search Console", gsc_flow)

    def report_flow():
        r = client.get("/reports/monthly", headers=headers, params={"days": 30})
        if r.status_code != 200:
            raise RuntimeError(f"report {r.status_code}: {r.text[:200]}")
        ok("Monthly report JSON", f"keywords={r.json()['summary'].get('active_keywords')}")
        r = client.get("/reports/monthly/export", headers=headers, params={"days": 30})
        if r.status_code != 200 or len(r.text) < 50:
            raise RuntimeError(f"export {r.status_code}")
        ok("Monthly report export", f"bytes={len(r.text)}")

    step("Reports", report_flow)

    def audit_flow():
        r = client.post("/seo/audits", headers=headers, json={"max_pages": 3})
        if r.status_code != 201:
            raise RuntimeError(f"audit {r.status_code}: {r.text[:300]}")
        d = r.json()
        ok("SEO batch audit", f"scanned={d.get('scanned_pages')} avg={d.get('avg_score')}")
        r = client.get("/seo/audits", headers=headers)
        if r.status_code != 200:
            raise RuntimeError(f"audit list {r.status_code}")
        ok("Audit history", f"count={len(r.json())}")

    step("SEO audit", audit_flow)

    def member_flow():
        r = client.get(f"/sites/{site_id}/members", headers=headers)
        if r.status_code != 200:
            raise RuntimeError(f"members {r.status_code}")
        ok("Site members", f"count={len(r.json())}")

    step("Site members", member_flow)

    print("\n" + "=" * 60)
    print(f"DONE: {passed} passed, {failed} failed")
    print("=" * 60)
    return 1 if failed else 0


def _health(client: httpx.Client) -> None:
    r = client.get("http://127.0.0.1:8000/docs")
    if r.status_code != 200:
        raise RuntimeError(f"backend unreachable: {r.status_code}")
    ok("Backend up", "http://127.0.0.1:8000")


if __name__ == "__main__":
    sys.exit(main())
