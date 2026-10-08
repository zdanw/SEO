"""P4：生产配置校验。"""
from __future__ import annotations

import pytest

from app.core.config import Settings

_STRONG = dict(
    APP_ENV="prod",
    APP_DEBUG=False,
    SECRET_KEY="x" * 40,
    DB_PASSWORD="S7rong!DbPass",
    DATABASE_URL="postgresql+psycopg2://seo:S7rong!DbPass@db:5432/seo_platform",
    REDIS_PASSWORD="S7rong!Redis",
    CELERY_BROKER_URL="redis://:S7rong!Redis@redis:6379/1",
    CELERY_RESULT_BACKEND="redis://:S7rong!Redis@redis:6379/2",
)


def test_production_rejects_debug_and_weak_secret():
    s = Settings(APP_ENV="prod", APP_DEBUG=True, SECRET_KEY="change-me-in-production-please-123456")
    with pytest.raises(RuntimeError, match="APP_DEBUG"):
        s.validate_production()


def test_production_rejects_default_db_password():
    s = Settings(**{**_STRONG, "DB_PASSWORD": "seo_dev_pass"})
    with pytest.raises(RuntimeError, match="DB_PASSWORD"):
        s.validate_production()


def test_production_rejects_weak_database_url_password():
    s = Settings(**{**_STRONG, "DATABASE_URL": "postgresql+psycopg2://seo:weak@db:5432/seo_platform"})
    with pytest.raises(RuntimeError, match="DATABASE_URL"):
        s.validate_production()


def test_production_rejects_default_redis_password():
    s = Settings(**{**_STRONG, "REDIS_PASSWORD": "seo_dev_redis"})
    with pytest.raises(RuntimeError, match="REDIS_PASSWORD"):
        s.validate_production()


def test_production_rejects_broker_without_password():
    s = Settings(**{**_STRONG, "CELERY_BROKER_URL": "redis://redis:6379/1"})
    with pytest.raises(RuntimeError, match="CELERY_BROKER_URL"):
        s.validate_production()


def test_production_rejects_cors_wildcard():
    s = Settings(**_STRONG, CORS_ORIGINS="*")
    with pytest.raises(RuntimeError, match="CORS"):
        s.validate_production()


def test_production_accepts_strong_config():
    s = Settings(**_STRONG, CORS_ORIGINS="https://app.example.com")
    s.validate_production()


def test_cors_origins_prod_defaults_to_frontend():
    s = Settings(APP_ENV="prod", CORS_ORIGINS="", FRONTEND_URL="https://app.example.com")
    assert s.cors_origins_list() == ["https://app.example.com"]
