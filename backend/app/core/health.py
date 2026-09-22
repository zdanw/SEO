"""深度健康检查：Postgres + Redis。"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import text

from app.core.config import settings


def build_health_report(
    *,
    db_ok: bool | None,
    redis_ok: bool | None,
    broker_ok: bool | None,
) -> dict[str, Any]:
    checks = {
        "database": "ok" if db_ok else ("error" if db_ok is False else "unknown"),
        "redis": "ok" if redis_ok else ("error" if redis_ok is False else "unknown"),
        "celery_broker": "ok" if broker_ok else ("error" if broker_ok is False else "unknown"),
    }
    critical_failed = db_ok is False or redis_ok is False
    status = "degraded" if critical_failed else "ok"
    return {
        "status": status,
        "app": settings.APP_NAME,
        "env": settings.APP_ENV,
        "time": datetime.now().isoformat(),
        "checks": checks,
    }


def probe_database() -> bool:
    from app.core.database import engine

    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


def probe_redis() -> bool:
    from app.utils.rate_limiter import get_redis

    try:
        return bool(get_redis().ping())
    except Exception:
        return False


def probe_celery_broker() -> bool:
    from app.utils.celery_redis import get_celery_broker_redis

    try:
        return bool(get_celery_broker_redis().ping())
    except Exception:
        return False


def get_health_snapshot() -> dict[str, Any]:
    return build_health_report(
        db_ok=probe_database(),
        redis_ok=probe_redis(),
        broker_ok=probe_celery_broker(),
    )
