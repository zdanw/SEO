"""Test Google Search Console OAuth configuration and /gsc/oauth/start endpoint."""
from __future__ import annotations

import sys
from urllib.parse import parse_qs, urlparse

import requests

BASE = "http://127.0.0.1:8000/api/v1"
EMAIL = "system_check@test.com"
PWD = "testpass123"


def main() -> int:
    print("=== GSC OAuth Test ===\n")

    try:
        r = requests.get("http://127.0.0.1:8000/api/health", timeout=5)
        r.raise_for_status()
        print("OK  backend health")
    except Exception as exc:
        print(f"FAIL backend not running: {exc}")
        return 1

    try:
        r = requests.post(f"{BASE}/auth/login", json={"email": EMAIL, "password": PWD}, timeout=10)
        if r.status_code == 401:
            r = requests.post(
                f"{BASE}/auth/register",
                json={"email": EMAIL, "password": PWD, "full_name": "Checker"},
                timeout=10,
            )
            r.raise_for_status()
            r = requests.post(f"{BASE}/auth/login", json={"email": EMAIL, "password": PWD}, timeout=10)
        r.raise_for_status()
        token = r.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}
        print("OK  login")
    except Exception as exc:
        print(f"FAIL login: {exc}")
        return 1

    try:
        r = requests.get(f"{BASE}/gsc/status", headers=headers, timeout=10)
        r.raise_for_status()
        status = r.json()
        print(f"OK  gsc status configured={status['configured']} connected={status['connected']}")
        if not status["configured"]:
            print("FAIL GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET missing")
            return 1
    except Exception as exc:
        print(f"FAIL gsc status: {exc}")
        return 1

    try:
        r = requests.get(f"{BASE}/gsc/oauth/start", headers=headers, timeout=10)
        r.raise_for_status()
        auth_url = r.json()["auth_url"]
        parsed = urlparse(auth_url)
        params = parse_qs(parsed.query)

        checks = [
            ("host", parsed.netloc == "accounts.google.com"),
            ("client_id", bool(params.get("client_id"))),
            ("redirect_uri", bool(params.get("redirect_uri"))),
            ("scope", any("webmasters" in s for s in params.get("scope", []))),
            ("state", bool(params.get("state"))),
            ("access_type", params.get("access_type") == ["offline"]),
        ]
        for name, ok in checks:
            print(f"{'OK' if ok else 'FAIL'}  auth_url.{name}")
            if not ok:
                return 1

        redirect = params["redirect_uri"][0]
        print(f"\nAuth URL ready:")
        print(auth_url[:140] + "...")
        print(f"\nRedirect URI: {redirect}")
    except Exception as exc:
        print(f"FAIL oauth start: {exc}")
        return 1

    print("\n=== Automated checks passed ===")
    print("Manual step: open http://127.0.0.1:5173/search-console and click Connect Google.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
