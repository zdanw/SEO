from celery import Celery
from celery.schedules import crontab

from app.core.config import settings

celery_app = Celery(
    "seo_platform",
    broker=settings.CELERY_BROKER_URL,
    backend=settings.CELERY_RESULT_BACKEND,
    include=[
        "app.tasks.serp_tasks",
        "app.tasks.alert_tasks",
        "app.tasks.reddit_tasks",
    ],
)

celery_app.conf.update(
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
        # 每 6 小时全量抓取 SERP
        "crawl-all-keywords": {
            "task": "app.tasks.serp_tasks.crawl_all_keywords",
            "schedule": crontab(minute=0, hour="*/6"),
        },
        # 每日 09:30 检查 SERP 失败率
        "check-serp-failures": {
            "task": "app.tasks.alert_tasks.check_serp_failures",
            "schedule": crontab(minute=30, hour=9),
        },
        # 每 30 分钟代理池健康检测
        "proxy-health-check": {
            "task": "app.tasks.serp_tasks.proxy_health_check",
            "schedule": crontab(minute="*/30"),
        },
        # Reddit：每 15 分钟发布到期的定时帖子/评论
        "reddit-publish-scheduled": {
            "task": "app.tasks.reddit_tasks.publish_scheduled_reddit_content",
            "schedule": crontab(minute="*/15"),
        },
        # Reddit：每 5 分钟回收卡住的 posting
        "reddit-recover-stale-posting": {
            "task": "app.tasks.reddit_tasks.recover_stale_reddit_posting",
            "schedule": crontab(minute="*/5"),
        },
        # Reddit：每日 08:00 汇总已发布帖子互动数据
        "reddit-sync-post-metrics": {
            "task": "app.tasks.reddit_tasks.sync_reddit_post_metrics",
            "schedule": crontab(minute=0, hour=8),
        },
        # Reddit：每日 09:00 账号健康检查与风控预警
        "reddit-account-health": {
            "task": "app.tasks.reddit_tasks.reddit_account_health_check",
            "schedule": crontab(minute=0, hour=9),
        },
        # Reddit：每 3 小时智能发现讨论并入待审队列
        "reddit-smart-discover": {
            "task": "app.tasks.reddit_tasks.discover_reddit_discussions",
            "schedule": crontab(minute=20, hour="*/3"),
        },
    },
)
