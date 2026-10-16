"""Reddit 自动化 Campaign Celery 任务。

- campaign_tick: beat 每 15 分钟调度，扫描 campaign 生成/对账/入队动作
- execute_campaign_action: 执行单条到期点赞（claim → vote → 落 RedditVoteLog）
"""
from __future__ import annotations

import logging

from celery.exceptions import SoftTimeLimitExceeded

from app.core.celery_app import celery_app
from app.core.database import SessionLocal

logger = logging.getLogger(__name__)

_TICK_SOFT_LIMIT = 540
_TICK_HARD_LIMIT = 570
_ACTION_SOFT_LIMIT = 55
_ACTION_HARD_LIMIT = 70


def _try_enqueue_action(action_id: int, task) -> bool:
    """按动作 ID 去重入队（同 publish 链路的 Redis 锁模式）。"""
    from app.utils.rate_limiter import get_redis

    r = get_redis()
    key = f"enqueue:reddit:campaign_action:{action_id}"
    if not r.set(key, "1", nx=True, ex=14 * 60):
        return False
    try:
        task.apply_async(args=[action_id], task_id=f"reddit-campaign-action-{action_id}")
        return True
    except Exception:
        r.delete(key)
        raise


@celery_app.task(
    name="app.tasks.reddit_campaign_tasks.campaign_tick",
    soft_time_limit=_TICK_SOFT_LIMIT,
    time_limit=_TICK_HARD_LIMIT,
)
def campaign_tick() -> dict:
    """Campaign 调度入口：单飞锁防重叠，tick 内完成计划/对账/回收/入队。"""
    from app.services.reddit_campaign import (
        due_upvote_action_ids,
        run_campaign_tick,
    )

    r = None
    try:
        from app.utils.rate_limiter import get_redis

        r = get_redis()
    except Exception:
        logger.debug("redis unavailable for campaign tick lock", exc_info=True)

    lock_key = "lock:reddit:campaign_tick"
    acquired = True
    if r is not None:
        acquired = bool(r.set(lock_key, "1", nx=True, ex=600))
    if not acquired:
        logger.info("Campaign tick skipped: previous tick still running")
        return {"ok": False, "reason": "locked"}

    db = SessionLocal()
    try:
        summary = run_campaign_tick(db)
        due_ids = due_upvote_action_ids(db)
        queued = 0
        for action_id in due_ids:
            if _try_enqueue_action(action_id, execute_campaign_action):
                queued += 1
        # 全局心跳：供 check_campaign_tick_heartbeat 告警与 ops /queues 判断调度是否停摆
        from app.services.reddit_campaign import write_campaign_heartbeat

        write_campaign_heartbeat()
        if queued or any(summary.values()):
            logger.info("Campaign tick done: %s, queued actions=%d", summary, queued)
        return {"ok": True, **summary, "queued_actions": queued}
    finally:
        if r is not None:
            try:
                r.delete(lock_key)
            except Exception:
                pass
        db.close()


_TICK_TRIGGER_DEBOUNCE = 60  # 秒；短时间多次操作只触发一轮


def trigger_campaign_tick() -> bool:
    """业务事件（启动/审批）后立即触发一轮 tick，消除最长 15 分钟的拾取延迟。

    tick 自身幂等（单飞锁 + 状态机），多触发无副作用；60s 防抖避免操作风暴重复入队；
    broker 不可用时静默降级为等待 beat 定时触发，不影响 API 响应。
    """
    try:
        from app.utils.rate_limiter import get_redis

        r = get_redis()
        key = "trigger:reddit:campaign_tick"
        if not r.set(key, "1", nx=True, ex=_TICK_TRIGGER_DEBOUNCE):
            return False
        try:
            campaign_tick.apply_async()
            return True
        except Exception:
            r.delete(key)  # 入队失败清除防抖，下次操作可重试
            raise
    except Exception:
        logger.warning("trigger_campaign_tick failed; fallback to beat schedule", exc_info=True)
        return False


_TICK_FALLBACK_LOCK = "trigger:reddit:campaign_tick_fallback"


def maybe_trigger_tick_on_activity() -> None:
    """beat 停摆兜底：读接口活动时若 tick 心跳已过期则补触发一轮 tick。

    单容器部署（魔搭）下 beat 进程一旦死亡，15 分钟调度整体停摆且没有任何
    自愈信号——心跳告警任务本身也是 beat 任务，会跟着一起停（自我掩蔽）。
    这里借 API 流量作为最后防线：心跳过期说明调度已停，补触发一次 tick，
    恢复排期与到期动作入队。心跳正常时只多一次 Redis GET，零额外开销；
    任何失败静默，绝不影响接口主流程。
    """
    try:
        from app.services.reddit_campaign import read_campaign_tick_heartbeat
        from app.utils.rate_limiter import get_redis

        if not read_campaign_tick_heartbeat().get("stale"):
            return
        r = get_redis()
        # 独立 120s 锁：并发请求/多个接口同时命中只补触发一轮
        if not r.set(_TICK_FALLBACK_LOCK, "1", nx=True, ex=120):
            return
        try:
            campaign_tick.apply_async()
            logger.warning(
                "Campaign tick heartbeat stale, fallback tick triggered by API activity"
            )
        except Exception:
            r.delete(_TICK_FALLBACK_LOCK)
    except Exception:  # noqa: BLE001 — 兜底路径静默降级
        logger.debug("fallback campaign tick trigger failed", exc_info=True)


@celery_app.task(
    name="app.tasks.reddit_campaign_tasks.execute_campaign_action",
    bind=True,
    soft_time_limit=_ACTION_SOFT_LIMIT,
    time_limit=_ACTION_HARD_LIMIT,
    max_retries=2,
)
def execute_campaign_action(self, action_id: int) -> dict:
    """执行单条点赞动作；429 限流/503 熔断按 retry_after 延迟重试，耗尽标 failed。"""
    from app.services.reddit_campaign import execute_action
    from app.services.reddit_client import RedditActionRetryable

    db = SessionLocal()
    try:
        try:
            return execute_action(db, action_id, attempt=self.request.retries)
        except RedditActionRetryable as exc:
            logger.warning(
                "Campaign action #%s rate-limited, auto retry %d/2 in %ss",
                action_id,
                exc.attempt + 1,
                exc.retry_after,
            )
            db.rollback()
            raise self.retry(exc=exc, countdown=exc.retry_after) from exc
        except SoftTimeLimitExceeded:
            db.rollback()
            from app.services.reddit_campaign import _finish_action
            from app.models.reddit import RedditCampaignAction

            action = db.get(RedditCampaignAction, action_id)
            if action is not None and action.status == "executing":
                _finish_action(db, action, "failed", error_message="执行超时，可重试")
            logger.warning("Campaign action #%s soft-timeout", action_id)
            return {"ok": False, "reason": "timeout"}
    finally:
        try:
            from app.utils.rate_limiter import get_redis

            get_redis().delete(f"enqueue:reddit:campaign_action:{action_id}")
        except Exception:
            pass
        db.close()


@celery_app.task(
    name="app.tasks.reddit_campaign_tasks.expand_communities",
    soft_time_limit=_TICK_SOFT_LIMIT,
    time_limit=_TICK_HARD_LIMIT,
)
def expand_communities() -> dict:
    """扩池：从优质人设社区扩散相似社区并校验入库（beat 每日）。"""
    from app.services.reddit_campaign import run_community_expansion

    db = SessionLocal()
    try:
        return run_community_expansion(db)
    finally:
        db.close()


@celery_app.task(
    name="app.tasks.reddit_campaign_tasks.community_feedback_calibration",
    soft_time_limit=_TICK_SOFT_LIMIT,
    time_limit=_TICK_HARD_LIMIT,
)
def community_feedback_calibration() -> dict:
    """表现反馈校准（beat 每日）：低表现 account×社区 降权 + 冷却。

    原 run_campaign_tick 每 15 分钟全量扫描一次，社区表现变化慢，
    降频到每日（紧随每日指标同步之后执行），扫描查询已加 30 天回看窗口。
    """
    from app.models.client_site import ClientSite
    from app.services.reddit_community_rotation import apply_feedback_calibration

    db = SessionLocal()
    calibrated = 0
    try:
        site_ids = [row[0] for row in db.query(ClientSite.id).all()]
        for site_id in site_ids:
            try:
                calibrated += apply_feedback_calibration(db, site_id=site_id)
            except Exception:  # noqa: BLE001 — 单站点失败不阻断其他
                db.rollback()
                logger.exception("Feedback calibration failed site=%s", site_id)
        return {"sites": len(site_ids), "calibrated": calibrated}
    finally:
        db.close()
