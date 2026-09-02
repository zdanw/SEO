"""社交平台统一适配层。

PulseForge 为主对接平台；同时支持 LinkedIn / Twitter / Facebook / Reddit 的统一接口。
当某平台无开放 API 时，回退到 mock 模式（记录请求但不实际发送），便于开发测试。

所有平台客户端遵循统一接口 SocialPlatformClient：
- publish_post(payload) -> PlatformPostResult
- get_post(post_id) -> engagement dict
- delete_post(post_id) -> bool
"""
from __future__ import annotations

import logging
import random
import string
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional

import httpx

from app.core.config import settings
from app.utils.rate_limiter import (
    acquire_token, RateLimitExceeded,
    CircuitBreaker, CircuitBreakerOpen,
)

logger = logging.getLogger(__name__)


# ============ 异常 ============
class PlatformError(RuntimeError):
    """平台 API 调用失败。"""


class PlatformNotConfiguredError(PlatformError):
    """平台未配置（缺 API Key / Token）。"""


class PlatformRateLimited(PlatformError):
    """被限流。"""


class PlatformCircuitOpen(PlatformError):
    """熔断器打开。"""


# ============ 数据结构 ============
@dataclass
class PublishPayload:
    """发帖请求载荷。"""
    title: str | None = None
    summary: str = ""
    image_url: str | None = None
    external_url: str | None = None
    hashtags: list[str] = field(default_factory=list)


@dataclass
class PlatformPostResult:
    """发帖结果。"""
    success: bool
    platform_post_id: str | None = None
    platform: str = ""
    raw_response: dict = field(default_factory=dict)
    error: str | None = None
    posted_at: datetime = field(default_factory=datetime.utcnow)


# ============ 统一接口 ============
class SocialPlatformClient:
    """社交平台客户端统一接口。"""

    platform_name: str = "base"

    def __init__(self, account_id: int, access_token: str | None, config: dict | None = None) -> None:
        self.account_id = account_id
        self.access_token = access_token
        self.config = config or {}

    def publish_post(self, payload: PublishPayload) -> PlatformPostResult:
        """发布一条帖子。子类实现。"""
        raise NotImplementedError

    def get_engagement(self, platform_post_id: str) -> dict:
        """获取互动数据。子类实现。"""
        raise NotImplementedError


# ============ PulseForge 客户端 ============
class PulseForgeClient(SocialPlatformClient):
    """PulseForge 平台客户端。

    若 PULSEFORGE_API_BASE 已配置，走真实 API；
    否则进入 mock 模式，生成假 platform_post_id，便于开发联调。
    """

    platform_name = "pulseforge"

    def __init__(self, account_id: int, access_token: str | None, config: dict | None = None) -> None:
        super().__init__(account_id, access_token, config)
        self.api_base = settings.PULSEFORGE_API_BASE
        self.api_key = settings.PULSEFORGE_API_KEY or access_token
        self.timeout = settings.PULSEFORGE_TIMEOUT
        self._mock_mode = not bool(self.api_base)

    def publish_post(self, payload: PublishPayload) -> PlatformPostResult:
        # 1. 限流
        if not acquire_token("pulseforge", self.account_id):
            raise PlatformRateLimited(f"PulseForge 账号 {self.account_id} 被限流")

        # 2. 熔断
        breaker = CircuitBreaker("pulseforge", self.account_id)
        if not breaker.allow_request():
            raise PlatformCircuitOpen(f"PulseForge 账号 {self.account_id} 熔断器打开")

        try:
            if self._mock_mode:
                return self._mock_publish(payload)

            result = self._real_publish(payload)
            breaker.record_success()
            return result
        except Exception as e:
            breaker.record_failure()
            logger.warning("PulseForge 发帖失败 account=%s: %s", self.account_id, e)
            raise

    def _real_publish(self, payload: PublishPayload) -> PlatformPostResult:
        """调用真实 PulseForge API。"""
        url = f"{self.api_base.rstrip('/')}/api/v1/posts"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        body = {
            "title": payload.title,
            "content": payload.summary,
            "image_url": payload.image_url,
            "external_url": payload.external_url,
            "hashtags": payload.hashtags,
        }
        with httpx.Client(timeout=self.timeout) as client:
            resp = client.post(url, headers=headers, json=body)
        if resp.status_code >= 400:
            raise PlatformError(f"PulseForge API {resp.status_code}: {resp.text[:300]}")
        data = resp.json()
        return PlatformPostResult(
            success=True,
            platform_post_id=str(data.get("id", "")),
            platform=self.platform_name,
            raw_response=data,
        )

    def _mock_publish(self, payload: PublishPayload) -> PlatformPostResult:
        """Mock 模式：生成假 ID，模拟成功。"""
        mock_id = "pf_mock_" + "".join(random.choices(string.ascii_lowercase + string.digits, k=12))
        return PlatformPostResult(
            success=True,
            platform_post_id=mock_id,
            platform=self.platform_name,
            raw_response={
                "mock": True,
                "title": payload.title,
                "summary": payload.summary[:80],
                "external_url": payload.external_url,
            },
        )

    def get_engagement(self, platform_post_id: str) -> dict:
        if self._mock_mode:
            return {
                "likes": random.randint(0, 50),
                "comments": random.randint(0, 10),
                "shares": random.randint(0, 20),
                "clicks": random.randint(0, 100),
                "mock": True,
            }
        url = f"{self.api_base.rstrip('/')}/api/v1/posts/{platform_post_id}/engagement"
        headers = {"Authorization": f"Bearer {self.api_key}"}
        with httpx.Client(timeout=self.timeout) as client:
            resp = client.get(url, headers=headers)
        if resp.status_code >= 400:
            raise PlatformError(f"PulseForge API {resp.status_code}: {resp.text[:300]}")
        return resp.json()


# ============ LinkedIn 客户端（mock 优先，预留真实 API） ============
class LinkedInClient(SocialPlatformClient):
    platform_name = "linkedin"

    def publish_post(self, payload: PublishPayload) -> PlatformPostResult:
        if not acquire_token("linkedin", self.account_id):
            raise PlatformRateLimited(f"LinkedIn 账号 {self.account_id} 被限流")
        breaker = CircuitBreaker("linkedin", self.account_id)
        if not breaker.allow_request():
            raise PlatformCircuitOpen(f"LinkedIn 账号 {self.account_id} 熔断器打开")
        try:
            # 真实 API 需访问 https://api.linkedin.com/v2/ugcPosts
            # 当前未配置 LinkedIn 凭证，走 mock
            mock_id = "li_mock_" + "".join(random.choices(string.ascii_lowercase + string.digits, k=12))
            breaker.record_success()
            return PlatformPostResult(
                success=True,
                platform_post_id=mock_id,
                platform=self.platform_name,
                raw_response={"mock": True, "title": payload.title},
            )
        except Exception as e:
            breaker.record_failure()
            raise

    def get_engagement(self, platform_post_id: str) -> dict:
        return {"likes": random.randint(0, 80), "comments": random.randint(0, 15), "mock": True}


# ============ Twitter/X 客户端 ============
class TwitterClient(SocialPlatformClient):
    platform_name = "twitter"

    def publish_post(self, payload: PublishPayload) -> PlatformPostResult:
        if not acquire_token("twitter", self.account_id):
            raise PlatformRateLimited(f"Twitter 账号 {self.account_id} 被限流")
        breaker = CircuitBreaker("twitter", self.account_id)
        if not breaker.allow_request():
            raise PlatformCircuitOpen(f"Twitter 账号 {self.account_id} 熔断器打开")
        try:
            mock_id = "tw_mock_" + "".join(random.choices(string.ascii_lowercase + string.digits, k=12))
            breaker.record_success()
            return PlatformPostResult(
                success=True,
                platform_post_id=mock_id,
                platform=self.platform_name,
                raw_response={"mock": True},
            )
        except Exception as e:
            breaker.record_failure()
            raise

    def get_engagement(self, platform_post_id: str) -> dict:
        return {"likes": random.randint(0, 200), "retweets": random.randint(0, 50), "mock": True}


# ============ Facebook 客户端 ============
class FacebookClient(SocialPlatformClient):
    platform_name = "facebook"

    def publish_post(self, payload: PublishPayload) -> PlatformPostResult:
        if not acquire_token("facebook", self.account_id):
            raise PlatformRateLimited(f"Facebook 账号 {self.account_id} 被限流")
        breaker = CircuitBreaker("facebook", self.account_id)
        if not breaker.allow_request():
            raise PlatformCircuitOpen(f"Facebook 账号 {self.account_id} 熔断器打开")
        try:
            mock_id = "fb_mock_" + "".join(random.choices(string.ascii_lowercase + string.digits, k=12))
            breaker.record_success()
            return PlatformPostResult(
                success=True,
                platform_post_id=mock_id,
                platform=self.platform_name,
                raw_response={"mock": True},
            )
        except Exception as e:
            breaker.record_failure()
            raise

    def get_engagement(self, platform_post_id: str) -> dict:
        return {"likes": random.randint(0, 100), "comments": random.randint(0, 30), "mock": True}


# ============ Reddit 客户端 ============
class RedditClient(SocialPlatformClient):
    platform_name = "reddit"

    def publish_post(self, payload: PublishPayload) -> PlatformPostResult:
        if not acquire_token("reddit", self.account_id):
            raise PlatformRateLimited(f"Reddit 账号 {self.account_id} 被限流")
        breaker = CircuitBreaker("reddit", self.account_id)
        if not breaker.allow_request():
            raise PlatformCircuitOpen(f"Reddit 账号 {self.account_id} 熔断器打开")
        try:
            mock_id = "rd_mock_" + "".join(random.choices(string.ascii_lowercase + string.digits, k=12))
            breaker.record_success()
            return PlatformPostResult(
                success=True,
                platform_post_id=mock_id,
                platform=self.platform_name,
                raw_response={"mock": True},
            )
        except Exception as e:
            breaker.record_failure()
            raise

    def get_engagement(self, platform_post_id: str) -> dict:
        return {"upvotes": random.randint(0, 300), "comments": random.randint(0, 80), "mock": True}


# ============ 客户端工厂 ============
_CLIENT_REGISTRY: dict[str, type[SocialPlatformClient]] = {
    "pulseforge": PulseForgeClient,
    "linkedin": LinkedInClient,
    "twitter": TwitterClient,
    "facebook": FacebookClient,
    "reddit": RedditClient,
}


def get_platform_client(
    platform: str,
    account_id: int,
    access_token: str | None,
    config: dict | None = None,
) -> SocialPlatformClient:
    """根据平台名获取对应客户端实例。"""
    platform_lower = platform.lower()
    cls = _CLIENT_REGISTRY.get(platform_lower)
    if not cls:
        raise PlatformNotConfiguredError(f"不支持的平台：{platform}（支持：{list(_CLIENT_REGISTRY.keys())}）")
    return cls(account_id=account_id, access_token=access_token, config=config)
