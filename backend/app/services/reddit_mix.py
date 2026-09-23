"""90/10 内容配额：近窗口内产品向内容不得超过 10%。"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

PROMO_RATIO_CAP = 0.10
_COUNTED_STATUSES = frozenset({"posted"})


class MixQuotaExceeded(ValueError):
    """再发布一条产品内容会超过 10% 上限。"""

    def __init__(self, message: str = "发布产品内容会超过近 7 天 10% 上限，请先发布人设讨论") -> None:
        super().__init__(message)


def can_enqueue_promo(*, casual_count: int, promo_count: int) -> bool:
    total_after = casual_count + promo_count + 1
    if total_after <= 0:
        return False
    return (promo_count + 1) / total_after <= PROMO_RATIO_CAP


def enforce_promo_quota(*, intent: str, casual_count: int, promo_count: int) -> None:
    if intent != "promo":
        return
    if not can_enqueue_promo(casual_count=casual_count, promo_count=promo_count):
        raise MixQuotaExceeded()


@dataclass
class MixCounts:
    casual: int = 0
    promo: int = 0

    @property
    def total(self) -> int:
        return self.casual + self.promo

    @property
    def promo_ratio(self) -> float:
        if not self.total:
            return 0.0
        return self.promo / self.total


def count_mix_window(db: Session, site_id: int, *, days: int = 7) -> MixCounts:
    """统计站点近 N 天已发布帖+评的 casual/promo 数量。待审/草稿不计入配额。"""
    from app.models.reddit import RedditComment, RedditPost

    since = datetime.utcnow() - timedelta(days=days)
    counts = MixCounts()
    for model in (RedditPost, RedditComment):
        rows = (
            db.query(model.content_intent)
            .filter(
                model.site_id == site_id,
                model.created_at >= since,
                model.status.in_(list(_COUNTED_STATUSES)),
            )
            .all()
        )
        for (intent,) in rows:
            if intent == "promo":
                counts.promo += 1
            else:
                counts.casual += 1
    return counts


def resolve_intent(*, community_purpose: str | None, include_site_url: bool = False) -> str:
    """产品版块或显式带链 → promo；其余为人设讨论。"""
    if include_site_url:
        return "promo"
    if community_purpose == "promo":
        return "promo"
    return "casual"


def resolve_post_intent(
    *,
    post_type: str,
    community_purpose: str | None,
    include_site_url: bool = False,
    allow_product: bool | None = None,
) -> str:
    """发帖意图：由「允许提及产品」开关决定；未传开关时兼容旧规则。"""
    if allow_product is not None:
        return "promo" if allow_product else "casual"
    if post_type in {"vent", "help_seek"}:
        return "casual"
    return resolve_intent(community_purpose=community_purpose, include_site_url=include_site_url)


def casual_mentions_brand(text: str, brands: list[str]) -> bool:
    lowered = (text or "").lower()
    return any(brand.lower() in lowered for brand in brands if brand and brand.strip())
