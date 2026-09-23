"""Check Zernio config and /reddit/oauth/start."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests

BASE = os.environ.get("API_BASE", "http://127.0.0.1:8000/api/v1")
EMAIL = os.environ.get("TEST_EMAIL", "admin@example.com")
PASSWORD = os.environ.get("TEST_PASSWORD", "admin123")


def main() -> None:
    print("=== Zernio Reddit Connect Test ===\n")

    from app.core.config import settings
    from app.core.database import SessionLocal
    from app.services.zernio_keys import is_zernio_ready, list_enabled_keys

    print(f"ZERNIO_API_BASE: {settings.ZERNIO_API_BASE}")
    db = SessionLocal()
    try:
        keys = list_enabled_keys(db, site_id=1)
        print(f"enabled zernio_api_keys (site 1): {len(keys)}")
        print(f"is_zernio_ready(db, 1): {is_zernio_ready(db, 1)}\n")
    finally:
        db.close()

    try:
        login = requests.post(
            f"{BASE}/auth/login",
            json={"email": EMAIL, "password": PASSWORD},
            timeout=10,
        )
        login.raise_for_status()
        token = login.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}
    except Exception as exc:
        print(f"SKIP oauth start (login failed): {exc}")
        return

    try:
        r = requests.get(f"{BASE}/reddit/status", headers=headers, timeout=10)
        print(f"GET /reddit/status -> {r.status_code}")
        print(r.text[:400])
        print()
        r = requests.get(f"{BASE}/reddit/oauth/start", headers=headers, timeout=10)
        print(f"GET /reddit/oauth/start -> {r.status_code}")
        print(r.text[:400])
        print()
        r = requests.get(f"{BASE}/reddit/zernio-keys", headers=headers, timeout=10)
        print(f"GET /reddit/zernio-keys -> {r.status_code}")
        print(r.text[:400])
    except Exception as exc:
        print(f"FAIL reddit smoke: {exc}")


if __name__ == "__main__":
    main()
