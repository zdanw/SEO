"""Reddit 自动化 Campaign Celery 任务。

- campaign_tick: beat 每 15 分钟调度，扫描 campaign 生成/对账/入队动作
- execute_campaign_action: 执行单条到期点赞（claim → vote → 落 RedditVoteLog）
"""
from __future__ import annotations

import logging
from datetime import datetime

from celery.exceptions import SoftTimeLimitExceeded

from app.core.celery_app import celery_app
from app.core.database import SessionLocal

logger = logging.getLogger(__name__)

_TICK_SOFT_LIMIT = 540
_TICK_HARD_LIMIT = 570
_ACTION_SOFT_LIMIT = 55
_ACTION_HARD_LIMIT = 70
# AI 生成流水线（检测+humanize 多轮重写）需要比执行动作更长的窗口
_GEN_SOFT_LIMIT = 280
_GEN_HARD_LIMIT = 320
_DISCOVERY_SOFT_LIMIT = 240
_DISCOVERY_HARD_LIMIT = 280

TICK_LAST_END_KEY = "metrics:reddit:campaign_tick:last_end"
_QUEUE_DEPTH_KEYS = ("default", "serp", "social", "generation")
_TICK_SLOT_MINUTES = 15  # beat 间隔；上轮结束距本轮触发不足该阈值的比例视为挤占


def _log_queue_depths() -> None:
    """阶段 0 埋点：各队列等待深度 + in-flight 代理值（Redis broker unacked zset）。"""
    import time as _time

    from app.utils.rate_limiter import get_redis

    started = _time.perf_counter()
    try:
        r = get_redis()
        depths = {name: int(r.llen(name) or 0) for name in _QUEUE_DEPTH_KEYS}
        depths["inflight_unacked"] = int(r.zcard("unacked_index") or 0)
        logger.info(
            "queue depths: %s (%.1fms)",
            depths,
            (_time.perf_counter() - started) * 1000,
            extra={"event": "queue_depth", "detail": depths},
        )
    except Exception:  # noqa: BLE001 — 观测附属，不影响主流程
        logger.debug("queue depth probe failed", exc_info=True)


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
        # 阶段 0 埋点：tick 重叠——上轮 tick 仍在运行，本轮被跳过（长 tick 挤占 beat 槽位的直接证据）
        logger.warning(
            "Campaign tick skipped: previous tick still running",
            extra={"event": "tick_overlap", "detail": {"lock_key": lock_key}},
        )
        return {"ok": False, "reason": "locked"}

    # 阶段 0 埋点：tick 挤占检测——上轮实际结束时刻距本轮 beat 触发过近，说明上轮
    # 接近跑满 15 分钟槽位（规划耗时逼近调度间隔的量化依据）
    try:
        if r is not None:
            raw = r.get(TICK_LAST_END_KEY)
            if raw:
                from app.core.timeutil import now_bj

                prev_end = datetime.fromisoformat(
                    raw.decode() if isinstance(raw, bytes) else str(raw)
                )
                gap_minutes = (now_bj() - prev_end).total_seconds() / 60
                if 0 <= gap_minutes < 2:
                    logger.warning(
                        "Campaign tick slot nearly exhausted: previous tick ended %.1f min ago",
                        gap_minutes,
                        extra={
                            "event": "tick_overrun",
                            "detail": {"gap_minutes": round(gap_minutes, 2)},
                        },
                    )
    except Exception:  # noqa: BLE001 — 观测附属，不影响主流程
        logger.debug("tick overrun probe failed", exc_info=True)

    db = SessionLocal()
    try:
        summary = run_campaign_tick(db)
        due_ids = due_upvote_action_ids(db)
        queued = 0
        for action_id in due_ids:
            if _try_enqueue_action(action_id, execute_campaign_action):
                queued += 1
        # 心跳重投：queued（可用）或 generating（租约过期）的骨架动作重新投递生成；
        # 消息丢失（规划已落库但投递失败/worker 丢弃）由此兜底
        try:
            from app.services.reddit_campaign import requeue_stale_generation_actions

            requeued_generation = requeue_stale_generation_actions(db)
            if requeued_generation:
                logger.info("Generation heartbeat requeued %d actions", requeued_generation)
        except Exception:  # noqa: BLE001 — 重投失败不阻断 tick（下轮再试）
            db.rollback()
            logger.exception("Generation heartbeat requeue failed")
        # 全局心跳：供 check_campaign_tick_heartbeat 告警与 ops /queues 判断调度是否停摆
        from app.services.reddit_campaign import write_campaign_heartbeat

        write_campaign_heartbeat()
        # 阶段 0 埋点：记录本轮实际结束时刻（供下轮挤占检测）+ 队列等待深度
        try:
            if r is not None:
                r.set(TICK_LAST_END_KEY, now_bj().isoformat(), ex=2 * 60 * 60)
        except Exception:  # noqa: BLE001
            logger.debug("tick last_end write failed", exc_info=True)
        _log_queue_depths()
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
    """执行单条点赞动作；429 限流/503 熔断按 retry_after 延迟重试，耗尽标 failed。

    阶段 1 协议：软超时/未知异常不再标 failed——请求可能已发出，标 reconciling
    走对账核实，防止超时后手动重试造成重复投票（P0-2）。
    """
    from app.services.reddit_campaign import (
        _worker_id as _task_worker_id,
        execute_action,
        get_action_lease_generation,
        mark_action_reconciling,
    )
    from app.services.reddit_client import RedditActionRetryable

    db = SessionLocal()
    try:
        try:
            return execute_action(
                db, action_id, attempt=self.request.retries, worker_id=_task_worker_id()
            )
        except RedditActionRetryable as exc:
            logger.warning(
                "Campaign action #%s rate-limited, auto retry %d in %ss",
                action_id,
                exc.attempt + 1,
                exc.retry_after,
            )
            db.rollback()
            raise self.retry(exc=exc, countdown=exc.retry_after) from exc
        except SoftTimeLimitExceeded:
            db.rollback()
            # 请求可能已发出：结果未知 → reconciling（对账分流），禁止自动重试
            if mark_action_reconciling(
                db, action_id, get_action_lease_generation(db, action_id), "执行软超时，结果未知待对账"
            ):
                logger.warning(
                    "Campaign action #%s soft-timeout -> reconciling", action_id
                )
            return {"ok": False, "reason": "timeout_reconciling"}
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


@celery_app.task(
    name="app.tasks.reddit_campaign_tasks.cleanup_campaign_data",
    soft_time_limit=_TICK_SOFT_LIMIT,
    time_limit=_TICK_HARD_LIMIT,
)
def cleanup_campaign_data() -> dict:
    """maintenance 队列：数据保留策略（beat 每日）——清理过期动作台账与尝试审计。

    分批删除（默认 5000/批）避免长事务；保留天数由配置控制，0 = 永久保留。
    """
    from app.core.config import settings
    from app.services.reddit_campaign import cleanup_expired_campaign_data

    db = SessionLocal()
    try:
        result = cleanup_expired_campaign_data(
            db,
            action_retention_days=settings.CAMPAIGN_ACTION_RETENTION_DAYS,
            attempt_retention_days=settings.CAMPAIGN_ATTEMPT_RETENTION_DAYS,
        )
        if any(result.values()):
            logger.info("Campaign data retention cleanup: %s", result)
        return {"ok": True, **result}
    finally:
        db.close()


@celery_app.task(
    name="app.tasks.reddit_campaign_tasks.generate_campaign_content",
    bind=True,
    soft_time_limit=_GEN_SOFT_LIMIT,
    time_limit=_GEN_HARD_LIMIT,
    max_retries=0,  # 重试由心跳重投统一驱动（幂等领取 + available_at 退避），任务层不自动 retry
)
def generate_campaign_content(self, action_id: int) -> dict:
    """generation 队列：单条骨架动作的 AI 生成（出环，不占 tick 窗口）。

    阶段 2 协议：AI 生成是纯读外部 API（无外部副作用承诺），任何失败路径都
    释放租约回 queued 退避重试或置 failed 终态，无需 reconciling。
    """
    from app.services.reddit_campaign import (
        _worker_id as _task_worker_id,
        generate_action_content,
    )

    db = SessionLocal()
    try:
        try:
            return generate_action_content(db, action_id, worker_id=_task_worker_id())
        except SoftTimeLimitExceeded:
            db.rollback()
            # AI 无外部副作用：释放租约回 queued，退避后由心跳重投
            from app.services.reddit_campaign import (
                get_generation_lease_generation,
                release_generation_lease,
            )

            released = release_generation_lease(
                db,
                action_id,
                get_generation_lease_generation(db, action_id),
                backoff_seconds=600,
                error_code="generation_soft_timeout",
                message="生成软超时，释放租约退避重试",
            )
            if released:
                logger.warning("Generation action #%s soft-timeout -> queued", action_id)
            return {"ok": False, "reason": "soft_timeout"}
    finally:
        try:
            from app.utils.rate_limiter import get_redis

            get_redis().delete(f"enqueue:reddit:campaign_generation:{action_id}")
        except Exception:
            pass
        db.close()


@celery_app.task(
    name="app.tasks.reddit_campaign_tasks.refresh_campaign_comment_candidates",
    soft_time_limit=_DISCOVERY_SOFT_LIMIT,
    time_limit=_DISCOVERY_HARD_LIMIT,
    max_retries=0,
)
def refresh_campaign_comment_candidates(campaign_id: int, account_id: int) -> dict:
    """serp 队列：campaign 评论候选发现（出环；账号级最小间隔去抖 + 结果缓存）。

    tick 只消费 Redis 候选缓存；发现调用（search/list）的耗时与 429 抖动
    不再占用 tick 窗口。账号级 429 退避沿用 _discovery_paused/_mark_discovery_backoff。
    """
    from app.services.reddit_campaign import run_comment_discovery

    db = SessionLocal()
    try:
        result = run_comment_discovery(db, campaign_id, account_id)
        if result.get("queued"):
            logger.info(
                "Campaign comment discovery refreshed: campaign=%s account=%s result=%s",
                campaign_id,
                account_id,
                result,
            )
        return result
    finally:
        db.close()
