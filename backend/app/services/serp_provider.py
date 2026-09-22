"""SERP Provider 契约与配额辅助。

统一入参/出参，便于切换 ScrapingBee、代理直抓、Mock，而不改 Celery 业务任务。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Optional, Protocol

from app.core.config import settings
from app.utils.rate_limiter import get_redis

# 统一状态语义：rank=NULL 时不得假装成「第 100 名」
CRAWL_STATUSES = frozenset({"success", "not_found", "blocked", "timeout", "error"})


@dataclass
class SerpQuery:
    keyword: str
    target_url: Optional[str] = None
    region: str = "us"
    language: str = "en"
    device: str = "desktop"
    competitor_domains: list[str] | None = None


@dataclass
class SerpFetchResult:
    """Provider 标准出参。"""

    keyword: str
    region: str
    organic_results: list[dict] = field(default_factory=list)
    target_rank: Optional[int] = None
    target_page: Optional[int] = None
    serp_features: dict = field(default_factory=dict)
    crawl_status: str = "success"
    error_message: Optional[str] = None
    proxy_used: Optional[str] = None
    provider: str = "unknown"
    duration_ms: Optional[int] = None
    estimated_cost: Optional[float] = None
    language: str = "en"
    device: str = "desktop"
    fetched_at: datetime = field(default_factory=datetime.utcnow)
    raw_truncated: Optional[str] = None


class SERPProvider(Protocol):
    name: str

    def fetch(self, query: SerpQuery) -> SerpFetchResult: ...


def normalize_fetch_result(result: SerpFetchResult, target_url: Optional[str]) -> SerpFetchResult:
    """固定状态语义，并写入可追溯元数据。"""
    status = result.crawl_status if result.crawl_status in CRAWL_STATUSES else "error"
    if status == "success":
        if target_url and result.target_rank is None:
            status = "not_found"
        elif result.target_rank is not None and result.target_rank < 1:
            status = "error"
            result.error_message = result.error_message or "invalid rank"
    # 非 success 时不应保留误导性排名（not_found 明确无排名）
    if status != "success":
        if status == "not_found":
            result.target_rank = None
            result.target_page = None
        elif status in ("blocked", "timeout", "error") and result.target_rank is None:
            pass

    features = dict(result.serp_features or {})
    features.update(
        {
            "provider": result.provider,
            "duration_ms": result.duration_ms,
            "estimated_cost": result.estimated_cost,
            "language": result.language,
            "device": result.device,
        }
    )
    if result.raw_truncated:
        features["raw_truncated"] = result.raw_truncated[:500]
    result.serp_features = features
    result.crawl_status = status
    return result


def serp_quota_key(day: date | None = None) -> str:
    d = day or date.today()
    return f"serp:quota:{d.isoformat()}"


def get_serp_quota_used(day: date | None = None) -> int:
    try:
        return int(get_redis().get(serp_quota_key(day)) or 0)
    except Exception:
        return 0


def get_serp_quota_remaining(day: date | None = None) -> int:
    limit = max(int(settings.SERP_DAILY_QUOTA), 0)
    if limit <= 0:
        return 10**9
    return max(limit - get_serp_quota_used(day), 0)


def try_consume_serp_quota(cost_units: int = 1) -> bool:
    """日配额原子扣减。超限返回 False。SERP_DAILY_QUOTA<=0 表示不限制。"""
    limit = int(settings.SERP_DAILY_QUOTA)
    if limit <= 0:
        return True
    r = get_redis()
    key = serp_quota_key()
    # INCR 后判断；超限则回滚
    used = int(r.incrby(key, cost_units))
    if used == cost_units:
        r.expire(key, 60 * 60 * 48)
    if used > limit:
        r.decrby(key, cost_units)
        return False
    # 累计估算费用
    try:
        cost_key = f"serp:cost:{date.today().isoformat()}"
        est = float(settings.SERP_EST_COST_PER_REQUEST) * cost_units
        r.incrbyfloat(cost_key, est)
        if used == cost_units:
            r.expire(cost_key, 60 * 60 * 48)
    except Exception:
        pass
    return True


def resolve_provider_name() -> str:
    """合规优先：默认 ScrapingBee；代理直抓需显式打开开关。"""
    if settings.SCRAPINGBEE_API_KEY:
        return "scrapingbee"
    if settings.SERP_ALLOW_PROXY_FALLBACK and (
        settings.PROXY_LIST or settings.PROXY_ENDPOINT
    ):
        return "proxy"
    return "mock"
