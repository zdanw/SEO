from app.core.celery_app import celery_app


@celery_app.task(name="app.tasks.demo.add", bind=True, max_retries=2)
def add(self, x: int, y: int) -> int:
    """演示任务：两数相加，用于验证 Celery worker 是否正常工作。"""
    return x + y


@celery_app.task(name="app.tasks.demo.ping")
def ping() -> str:
    return "pong"
