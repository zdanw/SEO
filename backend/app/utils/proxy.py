"""代理池管理。

支持两种模式：
1. 静态列表（PROXY_LIST=http://u:p@ip1:port,http://u:p@ip2:port）
2. 单一旋转端点（PROXY_ENDPOINT + PROXY_USERNAME + PROXY_PASSWORD，如 BrightData/Smartproxy）

无任何代理配置时返回 None（直连模式）。

健康检测：周期性请求 httpbin.org/ip，延迟 > 3s 标记不可用，5 分钟后自动恢复。
"""
from __future__ import annotations

import hashlib
import logging
import random
from dataclasses import dataclass
from typing import Optional

import httpx

from app.core.config import settings
from app.utils.rate_limiter import get_redis

logger = logging.getLogger(__name__)

HEALTH_CHECK_URL = "https://httpbin.org/ip"
HEALTH_TIMEOUT_S = 3.0
RECOVERY_TTL_S = 300  # 失败后 5 分钟才能再次使用


@dataclass
class ProxyInfo:
    url: str           # 完整 url 含认证：http://user:pass@ip:port
    provider: str      # brightdata/smartproxy/static/rotating
    is_healthy: bool = True
    last_check: float = 0.0


class ProxyPool:
    """代理池单例。"""

    def __init__(self) -> None:
        self._proxies: list[ProxyInfo] = []
        self._redis = get_redis()
        self._load()

    def _load(self) -> None:
        """从配置加载代理列表。"""
        if settings.PROXY_LIST:
            urls = [u.strip() for u in settings.PROXY_LIST.split(",") if u.strip()]
            self._proxies = [ProxyInfo(url=u, provider="static") for u in urls]
        elif settings.PROXY_ENDPOINT and settings.PROXY_USERNAME and settings.PROXY_PASSWORD:
            url = f"http://{settings.PROXY_USERNAME}:{settings.PROXY_PASSWORD}@{settings.PROXY_ENDPOINT}"
            self._proxies = [ProxyInfo(url=url, provider=settings.PROXY_PROVIDER or "rotating")]
        else:
            self._proxies = []  # 空池 → 直连

    @property
    def is_empty(self) -> bool:
        return len(self._proxies) == 0

    def get(self) -> Optional[ProxyInfo]:
        """随机取一个健康代理；空池返回 None（直连）。"""
        if not self._proxies:
            return None
        healthy = [p for p in self._proxies if self._is_healthy(p)]
        if not healthy:
            logger.warning("代理池无健康代理，回退直连")
            return None
        return random.choice(healthy)

    def _proxy_hash(self, proxy: ProxyInfo) -> str:
        return hashlib.md5(proxy.url.encode()).hexdigest()[:12]

    def _is_healthy(self, proxy: ProxyInfo) -> bool:
        key = f"proxy:unhealthy:{self._proxy_hash(proxy)}"
        return not bool(self._redis.get(key))

    def mark_failed(self, proxy: ProxyInfo) -> None:
        """标记代理失败，5 分钟内不再使用。"""
        key = f"proxy:unhealthy:{self._proxy_hash(proxy)}"
        self._redis.setex(key, RECOVERY_TTL_S, "1")

    def health_check_all(self) -> dict:
        """对所有代理做健康检测（可由 Celery Beat 周期触发）。"""
        results = {"total": len(self._proxies), "healthy": 0, "unhealthy": 0}
        for proxy in self._proxies:
            ok = self._check_one(proxy)
            if ok:
                results["healthy"] += 1
            else:
                results["unhealthy"] += 1
                self.mark_failed(proxy)
        return results

    def _check_one(self, proxy: ProxyInfo) -> bool:
        try:
            with httpx.Client(proxy=proxy.url, timeout=HEALTH_TIMEOUT_S) as c:
                r = c.get(HEALTH_CHECK_URL)
                return r.status_code == 200
        except Exception as e:
            logger.debug("代理健康检测失败 %s: %s", proxy.url, e)
            return False


_pool_singleton: ProxyPool | None = None


def get_proxy_pool() -> ProxyPool:
    global _pool_singleton
    if _pool_singleton is None:
        _pool_singleton = ProxyPool()
    return _pool_singleton
