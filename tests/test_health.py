"""P4：健康检查报告结构。"""
from __future__ import annotations

from app.core.health import build_health_report


def test_health_ok_when_deps_ok(monkeypatch):
    from app.core import config

    monkeypatch.setattr(config.settings, "APP_NAME", "SEO Platform")
    monkeypatch.setattr(config.settings, "APP_ENV", "dev")
    report = build_health_report(db_ok=True, redis_ok=True, broker_ok=True)
    assert report["status"] == "ok"
    assert report["checks"]["database"] == "ok"
    assert report["checks"]["redis"] == "ok"
    assert report["checks"]["celery_broker"] == "ok"


def test_health_degraded_when_db_down(monkeypatch):
    from app.core import config

    monkeypatch.setattr(config.settings, "APP_NAME", "SEO Platform")
    monkeypatch.setattr(config.settings, "APP_ENV", "dev")
    report = build_health_report(db_ok=False, redis_ok=True, broker_ok=True)
    assert report["status"] == "degraded"
    assert report["checks"]["database"] == "error"
