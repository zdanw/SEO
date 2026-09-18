"""限流与熔断模块。

基于 Redis 实现：
- Token Bucket 限流：每平台独立 QPS 限制
- 熔断器：失败率 > 阈值时自动熔断，cool-down 后半开试探

设计参考：Netflix Hystrix 的三态机（closed -> open -> half-open）。
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional

import redis

from app.core.config import settings


# ============ Redis 客户端单例 ============
_redis: redis.Redis | None = None


def get_redis() -> redis.Redis:
    global _redis
    if _redis is None:
        kwargs = {
            "host": settings.REDIS_HOST,
            "port": settings.REDIS_PORT,
            "db": settings.REDIS_DB,
            "decode_responses": True,
        }
        if settings.REDIS_PASSWORD:
            kwargs["password"] = settings.REDIS_PASSWORD
        _redis = redis.Redis(**kwargs)
    return _redis


# ============ 限流器 ============
class RateLimitExceeded(RuntimeError):
    """超出限流。"""


@dataclass
class TokenBucketConfig:
    """令牌桶配置。"""
    capacity: int = 5          # 桶容量（突发上限）
    refill_rate: float = 1.0   # 每秒补充令牌数（持续 QPS）


# 各平台默认限流配置
PLATFORM_LIMITS: dict[str, TokenBucketConfig] = {
    "serp": TokenBucketConfig(capacity=5, refill_rate=1.0),
    "reddit": TokenBucketConfig(capacity=3, refill_rate=1/10),      # 10 秒 1 次
    "default": TokenBucketConfig(capacity=5, refill_rate=1.0),
}


def acquire_token(platform: str, account_id: int | str) -> bool:
    """尝试获取一个令牌。返回 True 表示放行，False 表示被限流。

    使用 Redis Lua 脚本保证原子性。
    """
    cfg = PLATFORM_LIMITS.get(platform, PLATFORM_LIMITS["default"])
    r = get_redis()
    key = f"ratelimit:{platform}:{account_id}"
    now = time.time()

    # Lua 脚本：检查并消费令牌
    lua_script = """
    local key = KEYS[1]
    local capacity = tonumber(ARGV[1])
    local refill_rate = tonumber(ARGV[2])
    local now = tonumber(ARGV[3])
    local ttl = tonumber(ARGV[4])

    local data = redis.call('HMGET', key, 'tokens', 'last_refill')
    local tokens = tonumber(data[1])
    local last_refill = tonumber(data[2])

    if tokens == nil then
        tokens = capacity
        last_refill = now
    end

    -- 按时间补充令牌
    local elapsed = now - last_refill
    tokens = math.min(capacity, tokens + elapsed * refill_rate)

    if tokens >= 1 then
        tokens = tokens - 1
        redis.call('HMSET', key, 'tokens', tokens, 'last_refill', now)
        redis.call('EXPIRE', key, ttl)
        return 1
    else
        redis.call('HMSET', key, 'tokens', tokens, 'last_refill', now)
        redis.call('EXPIRE', key, ttl)
        return 0
    end
    """
    # TTL = 桶容量 / 补充速率 * 2，至少 60 秒
    ttl = max(int(cfg.capacity / cfg.refill_rate * 2), 60)
    result = r.eval(lua_script, 1, key, cfg.capacity, cfg.refill_rate, now, ttl)
    return bool(result)


# ============ 熔断器 ============
class CircuitBreakerOpen(RuntimeError):
    """熔断器已打开，请求被拒绝。"""


@dataclass
class BreakerConfig:
    """熔断器配置。"""
    failure_threshold: int = 5          # 连续失败次数阈值
    failure_rate_threshold: float = 0.5  # 失败率阈值（窗口内）
    min_requests: int = 10              # 触发失败率统计的最小请求数
    open_duration: int = 3600           # 熔断打开时长（秒），1 小时
    half_open_max: int = 3              # 半开状态最大试探请求数


BREAKER_CONFIGS: dict[str, BreakerConfig] = {
    "serp": BreakerConfig(),
    "reddit": BreakerConfig(failure_threshold=5, failure_rate_threshold=0.5, open_duration=3600),
    "default": BreakerConfig(),
}


class CircuitBreaker:
    """单平台单账号的熔断器。

    状态：
    - closed: 正常放行，记录成功/失败
    - open: 拒绝所有请求，等待 cool-down
    - half_open: 放行少量试探请求，成功则恢复，失败则重新 open
    """

    def __init__(self, platform: str, account_id: int | str, config: BreakerConfig | None = None) -> None:
        self.platform = platform
        self.account_id = account_id
        self.config = config or BREAKER_CONFIGS.get(platform, BREAKER_CONFIGS["default"])
        self._r = get_redis()
        self._state_key = f"breaker:state:{platform}:{account_id}"
        self._counter_key = f"breaker:counter:{platform}:{account_id}"
        self._opened_at_key = f"breaker:opened_at:{platform}:{account_id}"
        self._half_open_trials_key = f"breaker:half_trials:{platform}:{account_id}"

    def _get_state(self) -> str:
        return self._r.get(self._state_key) or "closed"

    def allow_request(self) -> bool:
        """检查是否允许请求通过。"""
        state = self._get_state()

        if state == "closed":
            return True

        if state == "open":
            # 检查是否到了 cool-down 时间
            opened_at = self._r.get(self._opened_at_key)
            if opened_at and (time.time() - float(opened_at)) >= self.config.open_duration:
                # 转入半开
                self._r.set(self._state_key, "half_open", ex=self.config.open_duration * 2)
                self._r.set(self._half_open_trials_key, 0, ex=self.config.open_duration * 2)
                return True
            return False

        if state == "half_open":
            # 限制试探请求数
            trials = int(self._r.get(self._half_open_trials_key) or 0)
            if trials < self.config.half_open_max:
                self._r.incr(self._half_open_trials_key)
                return True
            return False

        return True

    def record_success(self) -> None:
        """记录一次成功。"""
        state = self._get_state()
        if state == "half_open":
            # 半开状态下成功 -> 恢复 closed
            self._reset_to_closed()
        elif state == "closed":
            # 重置失败计数
            self._r.hset(self._counter_key, mapping={"success": 0, "failure": 0})

    def record_failure(self) -> None:
        """记录一次失败。"""
        state = self._get_state()

        if state == "half_open":
            # 半开状态下失败 -> 重新 open
            self._trip_open()
            return

        if state == "closed":
            # 累加失败计数
            self._r.hincrby(self._counter_key, "failure", 1)
            self._r.hincrby(self._counter_key, "total", 1)
            # 读取统计
            stats = self._r.hgetall(self._counter_key)
            failure = int(stats.get("failure", 0))
            total = int(stats.get("total", 0))
            success = total - failure

            # 检查连续失败阈值
            if failure >= self.config.failure_threshold:
                self._trip_open()
                return

            # 检查失败率
            if total >= self.config.min_requests:
                rate = failure / total
                if rate >= self.config.failure_rate_threshold:
                    self._trip_open()
                    return

    def _trip_open(self) -> None:
        """跳转到 open 状态。"""
        self._r.set(self._state_key, "open", ex=self.config.open_duration * 2)
        self._r.set(self._opened_at_key, time.time(), ex=self.config.open_duration * 2)
        # 重置计数器
        self._r.delete(self._counter_key)

    def _reset_to_closed(self) -> None:
        """恢复到 closed 状态。"""
        self._r.set(self._state_key, "closed", ex=self.config.open_duration * 2)
        self._r.delete(self._counter_key)
        self._r.delete(self._half_open_trials_key)

    def status(self) -> dict:
        """获取当前熔断器状态（用于监控）。"""
        state = self._get_state()
        stats = self._r.hgetall(self._counter_key) or {}
        return {
            "platform": self.platform,
            "account_id": str(self.account_id),
            "state": state,
            "success": int(stats.get("success", 0)),
            "failure": int(stats.get("failure", 0)),
            "total": int(stats.get("total", 0)),
        }
