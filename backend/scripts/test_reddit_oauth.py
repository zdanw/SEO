"""Test Reddit OAuth configuration and /reddit/oauth/start endpoint."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests

BASE = os.environ.get("API_BASE", "http://127.0.0.1:8000/api/v1")
EMAIL = os.environ.get("TEST_EMAIL", "admin@example.com")
PASSWORD = os.environ.get("TEST_PASSWORD", "admin123")


def main() -> None:
    print("=== Reddit OAuth Test ===\n")

    from app.core.config import settings
    from app.services.reddit_client import is_reddit_configured

    print(f"REDDIT_CLIENT_ID set: {bool(settings.REDDIT_CLIENT_ID)}")
    print(f"REDDIT_CLIENT_SECRET set: {bool(settings.REDDIT_CLIENT_SECRET)}")
    print(f"REDDIT_REDIRECT_URI: {settings.REDDIT_REDIRECT_URI}")
    print(f"REDDIT_USER_AGENT: {settings.REDDIT_USER_AGENT}")
    print(f"is_reddit_configured(): {is_reddit_configured()}\n")

    try:
        login = requests.post(
            f"{BASE}/auth/login",
            data={"username": EMAIL, "password": PASSWORD},
            timeout=10,
        )
        login.raise_for_status()
        token = login.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}
    except Exception as exc:
        print(f"SKIP oauth start (login failed): {exc}")
        return

    try:
        r = requests.get(f"{BASE}/reddit/oauth/start", headers=headers, timeout=10)
        print(f"GET /reddit/oauth/start -> {r.status_code}")
        if r.status_code == 200:
            data = r.json()
            print(f"auth_url prefix: {data.get('auth_url', '')[:80]}...")
        else:
            print(r.text[:300])
    except Exception as exc:
        print(f"FAIL oauth start: {exc}")


if __name__ == "__main__":
    main()
