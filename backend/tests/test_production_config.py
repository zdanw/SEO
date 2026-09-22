"""P4：生产配置校验。"""
from __future__ import annotations

import pytest

from app.core.config import Settings


def test_production_rejects_debug_and_weak_secret():
    s = Settings(APP_ENV="prod", APP_DEBUG=True, SECRET_KEY="change-me-in-production-please-123456")
    with pytest.raises(RuntimeError, match="APP_DEBUG"):
        s.validate_production()


def test_production_accepts_strong_secret():
    s = Settings(
        APP_ENV="production",
        APP_DEBUG=False,
        SECRET_KEY="x" * 40,
    )
    s.validate_production()


def test_cors_origins_prod_defaults_to_frontend():
    s = Settings(APP_ENV="prod", CORS_ORIGINS="", FRONTEND_URL="https://app.example.com")
    assert s.cors_origins_list() == ["https://app.example.com"]
