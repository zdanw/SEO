"""P4：ops 队列深度应读 Celery broker Redis，而非 REDIS_DB。"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from app.utils.celery_redis import (
    get_celery_broker_redis,
    get_celery_queue_depth,
    parse_redis_db_from_url,
)


def test_parse_redis_db_from_broker_url():
    assert parse_redis_db_from_url("redis://:pass@127.0.0.1:6379/1") == 1
    assert parse_redis_db_from_url("redis://localhost:6379/2") == 2
    assert parse_redis_db_from_url("redis://localhost:6379/") == 0
    assert parse_redis_db_from_url("redis://localhost:6379") == 0


def test_queue_depth_uses_broker_redis():
    broker = MagicMock()
    broker.llen.return_value = 7
    with patch("app.utils.celery_redis.get_celery_broker_redis", return_value=broker):
        assert get_celery_queue_depth("social") == 7
    broker.llen.assert_called_once_with("social")


def test_get_celery_broker_redis_selects_broker_db(monkeypatch):
    from app.core import config

    monkeypatch.setattr(
        config.settings,
        "CELERY_BROKER_URL",
        "redis://:secret@10.0.0.1:6380/1",
    )
    captured = {}

    def fake_redis(**kwargs):
        captured.update(kwargs)
        return MagicMock()

    with patch("app.utils.celery_redis.redis.Redis", side_effect=fake_redis):
        import app.utils.celery_redis as mod

        mod._broker_redis = None
        get_celery_broker_redis()

    assert captured["host"] == "10.0.0.1"
    assert captured["port"] == 6380
    assert captured["db"] == 1
    assert captured["password"] == "secret"
