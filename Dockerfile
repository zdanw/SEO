# SEO 平台后端「ModelScope 创空间」单容器镜像
# ---------------------------------------------------------------------------
# 魔搭创空间（Docker SDK）要求：仓库根目录有 Dockerfile，且服务监听 0.0.0.0:7860。
# 由于创空间只跑一个容器，这里把运行时依赖全部内置：
#   PostgreSQL 15 + TimescaleDB（时序扩展） + Redis + Celery(worker/beat) + FastAPI
# 由 start.sh 负责初始化并拉起全部进程（见 start.sh）。
#
# 与 docker-compose 生产栈使用的精简镜像区分开：那个是 Dockerfile.api。
# ---------------------------------------------------------------------------
FROM python:3.13-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    TZ=Asia/Shanghai \
    DEBIAN_FRONTEND=noninteractive

WORKDIR /srv/app

# ---------- 系统依赖：编译工具 + PostgreSQL15 + Redis ----------
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential libpq-dev curl ca-certificates gnupg lsb-release procps \
        postgresql-15 postgresql-client-15 redis-server \
    && rm -rf /var/lib/apt/lists/*

# ---------- TimescaleDB（PostgreSQL 15 扩展，官方 packagecloud 源）----------
RUN curl -fsSL https://packagecloud.io/timescale/timescaledb/gpgkey \
        | gpg --dearmor -o /usr/share/keyrings/timescaledb.gpg \
    && echo "deb [signed-by=/usr/share/keyrings/timescaledb.gpg] https://packagecloud.io/timescale/timescaledb/debian/ bookworm main" \
        > /etc/apt/sources.list.d/timescaledb.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends timescaledb-2-postgresql-15 \
    && rm -rf /var/lib/apt/lists/*

# 预创建 PG 集群目录，并确保 timescaledb 预加载（幂等）
RUN if [ ! -d /etc/postgresql/15/main ]; then pg_createcluster 15 main; fi; \
    grep -q timescaledb /etc/postgresql/15/main/postgresql.conf \
      || echo "shared_preload_libraries = 'timescaledb'" >> /etc/postgresql/15/main/postgresql.conf

# ---------- Python 依赖（先装，充分利用层缓存）----------
COPY requirements.txt ./
RUN pip install -r requirements.txt

# ---------- 应用代码 ----------
COPY alembic.ini ./
COPY alembic ./alembic
COPY app ./app
COPY start.sh ./
RUN chmod +x start.sh

# 创空间只暴露这一个端口
EXPOSE 7860

# start.sh 会先拉起 PG/Redis、跑迁移，再前台启动 FastAPI
CMD ["bash", "start.sh"]
