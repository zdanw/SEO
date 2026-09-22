"""Celery broker Redis 客户端（与业务 REDIS_DB 分离）。"""
from __future__ import annotations

from urllib.parse import urlparse

import redis

from app.core.config import settings

_broker_redis: redis.Redis | None = None


def parse_redis_db_from_url(url: str) -> int:
    """从 redis URL 解析 DB 编号，缺省为 0。"""
    path = (urlparse(url).path or "").lstrip("/")
    if not path:
        return 0
    try:
        return int(path.split("/")[0])
    except ValueError:
        return 0


def get_celery_broker_redis() -> redis.Redis:
    """连接 Celery broker 所在 Redis DB（默认 /1），用于查队列积压。"""
    global _broker_redis
    if _broker_redis is None:
        parsed = urlparse(settings.CELERY_BROKER_URL)
        kwargs: dict = {
            "host": parsed.hostname or settings.REDIS_HOST,
            "port": parsed.port or settings.REDIS_PORT,
            "db": parse_redis_db_from_url(settings.CELERY_BROKER_URL),
            "decode_responses": True,
        }
        if parsed.password:
            kwargs["password"] = parsed.password
        elif settings.REDIS_PASSWORD:
            kwargs["password"] = settings.REDIS_PASSWORD
        _broker_redis = redis.Redis(**kwargs)
    return _broker_redis


def get_celery_queue_depth(name: str) -> int | None:
    """读取 Celery 队列 list 长度；失败返回 None。"""
    try:
        return int(get_celery_broker_redis().llen(name))
    except Exception:
        return None
