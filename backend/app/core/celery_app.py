from celery import Celery
from celery.schedules import crontab

from app.core.config import settings

celery_app = Celery(
    "seo_platform",
    broker=settings.CELERY_BROKER_URL,
    backend=settings.CELERY_RESULT_BACKEND,
    include=[
        "app.tasks.serp_tasks",
        "app.tasks.social_tasks",
        "app.tasks.alert_tasks",
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
        "app.tasks.social_*": {"queue": "social"},
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
        # 每周一 03:00 全量外链检测
        "check-backlinks": {
            "task": "app.tasks.alert_tasks.check_backlinks_health",
            "schedule": crontab(minute=0, hour=3, day_of_week=1),
        },
        # 每 30 分钟代理池健康检测
        "proxy-health-check": {
            "task": "app.tasks.serp_tasks.proxy_health_check",
            "schedule": crontab(minute="*/30"),
        },
    },
)
