"""Quick system check: register/login and hit main API endpoints."""
from __future__ import annotations

import sys

import requests

BASE = "http://127.0.0.1:8000/api/v1"
errors: list[tuple[str, str]] = []


def test(name: str, fn) -> None:
    try:
        fn()
        print(f"OK  {name}")
    except Exception as e:
        print(f"FAIL {name}: {e}")
        errors.append((name, str(e)))


email = "system_check@test.com"
pwd = "testpass123"
r = requests.post(
    f"{BASE}/auth/register",
    json={"email": email, "password": pwd, "full_name": "Checker"},
    timeout=10,
)
if r.status_code == 400 and "已被注册" in r.text:
    r = requests.post(f"{BASE}/auth/login", json={"email": email, "password": pwd}, timeout=10)
elif r.status_code == 201:
    r = requests.post(f"{BASE}/auth/login", json={"email": email, "password": pwd}, timeout=10)
r.raise_for_status()
token = r.json()["access_token"]
H = {"Authorization": f"Bearer {token}"}


def get(path: str, **params):
    resp = requests.get(f"{BASE}{path}", headers=H, params=params, timeout=15)
    if resp.status_code >= 400:
        raise RuntimeError(f"{resp.status_code} {resp.text[:400]}")
    return resp.json()


def post_json(path: str, payload: dict | None = None):
    resp = requests.post(f"{BASE}{path}", headers=H, json=payload or {}, timeout=15)
    if resp.status_code >= 400:
        raise RuntimeError(f"{resp.status_code} {resp.text[:400]}")
    return resp.json() if resp.content else None


def delete(path: str):
    resp = requests.delete(f"{BASE}{path}", headers=H, timeout=15)
    if resp.status_code >= 400:
        raise RuntimeError(f"{resp.status_code} {resp.text[:400]}")


def test_keyword_delete():
    created = post_json("/keywords", {"keyword": "__delete_test__", "region": "us"})
    delete(f"/keywords/{created['id']}")


test("auth/me", lambda: get("/auth/me"))
test("dashboard/summary", lambda: get("/dashboard/summary"))
test("dashboard/rank-trends", lambda: get("/dashboard/rank-trends"))
test("dashboard/social-funnel", lambda: get("/dashboard/social-funnel"))
test("dashboard/backlink-stats", lambda: get("/dashboard/backlink-stats"))
test("dashboard/recommendations", lambda: get("/dashboard/recommendations"))
test("serp/config", lambda: get("/serp/config"))
test("serp/latest", lambda: get("/serp/latest"))
test("serp/snapshots", lambda: get("/serp/snapshots"))
def test_ai_requires_auth():
    resp = requests.post(
        f"{BASE}/ai/generate",
        json={"keyword": "test"},
        timeout=10,
    )
    if resp.status_code != 401:
        raise RuntimeError(f"expected 401, got {resp.status_code}")


test("keywords list", lambda: get("/keywords"))
test("keywords delete", test_keyword_delete)
test("ai auth required", test_ai_requires_auth)
test("articles list", lambda: get("/articles"))
test("social accounts", lambda: get("/social/accounts"))
test("gsc status", lambda: get("/gsc/status"))
test("backlinks", lambda: get("/backlinks"))
test("competitors", lambda: get("/competitors"))

print("---")
print(f"Total failures: {len(errors)}")
if errors:
    for n, e in errors:
        print(f"  {n}: {e}")
    sys.exit(1)
