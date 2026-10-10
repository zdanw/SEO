from celery import Celery
from celery.schedules import crontab
from celery.signals import setup_logging

from app.core.config import settings


@setup_logging.connect
def _configure_worker_logging(**_kwargs) -> None:
    """接管 Celery worker/beat 日志：复用统一格式（logging_config），
    避免 Celery 默认 hijack 根 logger 造成日志双份输出、格式割裂。"""
    from app.core.logging_config import setup_logging

    setup_logging(debug=settings.APP_DEBUG)


celery_app = Celery(
    "seo_platform",
    broker=settings.CELERY_BROKER_URL,
    backend=settings.CELERY_RESULT_BACKEND,
    include=[
        "app.tasks.serp_tasks",
        "app.tasks.alert_tasks",
        "app.tasks.reddit_tasks",
        "app.tasks.reddit_campaign_tasks",
    ],
)

celery_app.conf.update(
    worker_hijack_root_logger=False,  # 日志统一由 logging_config 接管（配合 setup_logging 信号）
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="Asia/Shanghai",
    enable_utc=True,
    task_track_started=True,
    task_time_limit=60 * 60,  # 单任务最长 1h
    task_soft_time_limit=55 * 60,
    worker_prefetch_multiplier=1,
    task_default_queue="default",
    task_routes={
        "app.tasks.serp_*": {"queue": "serp"},
        "app.tasks.reddit_*": {"queue": "social"},
    },
    beat_schedule={
        # Reddit：每 15 分钟发布到期的定时帖子/评论
        "reddit-publish-scheduled": {
            "task": "app.tasks.reddit_tasks.publish_scheduled_reddit_content",
            "schedule": crontab(minute="*/15"),
        },
        # Reddit：每 15 分钟 Campaign 调度（与发布扫描错峰）
        "reddit-campaign-tick": {
            "task": "app.tasks.reddit_campaign_tasks.campaign_tick",
            "schedule": crontab(minute="7,22,37,52"),
        },
        # Reddit：每 5 分钟回收卡住的 posting
        "reddit-recover-stale-posting": {
            "task": "app.tasks.reddit_tasks.recover_stale_reddit_posting",
            "schedule": crontab(minute="*/5"),
        },
        # 告警：每 5 分钟检查 Campaign 调度心跳（tick 停摆 → WARNING 日志）
        "reddit-campaign-heartbeat-check": {
            "task": "app.tasks.alert_tasks.check_campaign_tick_heartbeat",
            "schedule": crontab(minute="*/5"),
        },
        # Reddit：每天扩池（从优质人设社区扩散相似社区并校验入库）
        "reddit-expand-communities": {
            "task": "app.tasks.reddit_campaign_tasks.expand_communities",
            "schedule": crontab(hour=4, minute=17),
        },
        # 已移除的固定调度（按需可手动/API 触发，任务本身仍注册）：
        # SERP 全量抓取、代理健康检测、SERP 失败率告警、
        # Reddit 帖子指标同步、账号健康检查、智能发现讨论
    },
)
