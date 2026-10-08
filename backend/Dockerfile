# SEO 平台后端镜像（FastAPI + Celery 共用）
# 数据库驱动 psycopg2-binary / lxml / scrapy 等在 py3.13 下多数有 wheel，
# 保留 build-essential + libpq-dev 兜底源码编译场景。
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    TZ=Asia/Shanghai

WORKDIR /srv/app

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential libpq-dev curl \
    && rm -rf /var/lib/apt/lists/*

# 先装依赖，充分利用层缓存
COPY requirements.txt ./
RUN pip install -r requirements.txt

COPY alembic.ini ./
COPY alembic ./alembic
COPY app ./app

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/api/health || exit 1

# 生产多 worker；uvicorn 仅监听容器内 8000，由 Nginx 反代
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", \
     "--workers", "4", "--proxy-headers", "--forwarded-allow-ips", "*"]
