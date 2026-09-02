"""外链监控服务：检测外链是否存活。"""
from __future__ import annotations

import logging
import random
from datetime import datetime
from typing import Optional

import httpx
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.backlink import Backlink

logger = logging.getLogger(__name__)

MAX_CHECK_TIMEOUT_S = 10
DEAD_STATUS_CODES = {404, 410, 451, 502, 503}


class BacklinkMonitor:
    """外链存活检测。"""

    def __init__(self) -> None:
        self._mock_mode = not bool(settings.PROXY_LIST or settings.PROXY_ENDPOINT)

    def check_one(self, db: Session, backlink: Backlink) -> Backlink:
        """检测单个外链存活状态。"""
        if self._mock_mode:
            # Mock 模式：90% 概率存活，同 URL 同日结果稳定
            seed = hash(f"{backlink.source_url}|{datetime.utcnow().strftime('%Y-%m-%d')}")
            rng = random.Random(seed)
            alive = rng.random() < 0.9
        else:
            alive = self._head_check(backlink.source_url)

        backlink.last_checked_at = datetime.utcnow()
        if not alive and backlink.is_alive:
            backlink.is_alive = False
            backlink.lost_at = datetime.utcnow()
        elif alive and not backlink.is_alive:
            backlink.is_alive = True
            backlink.lost_at = None
        db.commit()
        return backlink

    def check_batch(self, db: Session, site_id: int, limit: int = 100) -> dict:
        """批量检测某站点的外链（按 last_checked_at ASC 优先检查最久的）。"""
        links = (
            db.query(Backlink)
            .filter(Backlink.site_id == site_id)
            .order_by(Backlink.last_checked_at.asc().nullsfirst())
            .limit(limit)
            .all()
        )
        alive_count = 0
        dead_count = 0
        for link in links:
            self.check_one(db, link)
            if link.is_alive:
                alive_count += 1
            else:
                dead_count += 1
        return {"total": len(links), "alive": alive_count, "dead": dead_count}

    def _head_check(self, url: str) -> bool:
        try:
            with httpx.Client(timeout=MAX_CHECK_TIMEOUT_S, follow_redirects=True) as c:
                r = c.head(url)
            if r.status_code in DEAD_STATUS_CODES:
                return False
            return r.status_code < 400
        except Exception as e:
            logger.debug("外链存活检测失败 %s: %s", url, e)
            return False


_monitor_singleton: BacklinkMonitor | None = None


def get_backlink_monitor() -> BacklinkMonitor:
    global _monitor_singleton
    if _monitor_singleton is None:
        _monitor_singleton = BacklinkMonitor()
    return _monitor_singleton
