#!/usr/bin/env bash
# ===========================================================================
# ModelScope 创空间（Docker SDK）单容器启动脚本
# ---------------------------------------------------------------------------
# 依次完成：
#   1) 生成/复用 SECRET_KEY，装配环境变量
#   2) 若无外部 DATABASE_URL，则拉起 PostgreSQL 15（含 TimescaleDB 预加载）、建库建角色
#   3) 拉起 Redis
#   4) 执行 alembic 迁移
#   5) 拉起 Celery worker + beat
#   6) 前台启动 FastAPI，监听 0.0.0.0:7860
#
# 所有变量均可用创空间「环境变量」覆盖（ms_deploy.json / 设置页）。
# ===========================================================================
set -euo pipefail

APP_DIR="/srv/app"
DATA_DIR="${SEO_DATA_DIR:-/var/lib/seo-platform}"
PG_VERSION="15"
PG_CLUSTER="main"
PG_CONF="/etc/postgresql/${PG_VERSION}/${PG_CLUSTER}/postgresql.conf"
SECRET_FILE="${DATA_DIR}/secret_key"

mkdir -p "${DATA_DIR}"

log() { echo "[start] $*"; }

# ---------- 1. 密钥与环境变量 ----------
if [ -z "${SECRET_KEY:-}" ]; then
  if [ -f "${SECRET_FILE}" ]; then
    SECRET_KEY="$(cat "${SECRET_FILE}")"
  else
    SECRET_KEY="$(head -c 48 /dev/urandom | base64 | tr -d '\n=+/')"
    printf '%s' "${SECRET_KEY}" > "${SECRET_FILE}"
  fi
fi
export SECRET_KEY

export APP_ENV="${APP_ENV:-production}"
export APP_DEBUG="${APP_DEBUG:-false}"

# 若创空间「环境变量」已提供外部 DATABASE_URL（非本机地址），则直接使用它并跳过本地
# PostgreSQL，避免下面的默认值把外部连接串覆盖掉。
IS_EXTERNAL_DB=0
case "${DATABASE_URL:-}" in
  ""|*127.0.0.1*|*localhost*) ;;
  *) IS_EXTERNAL_DB=1 ;;
esac

if [ "${IS_EXTERNAL_DB}" = "1" ]; then
  log "检测到外部 DATABASE_URL，跳过本地 PostgreSQL"
else
  export DB_HOST="127.0.0.1"
  export DB_PORT="5432"
  export DB_USER="${DB_USER:-seo}"
  export DB_PASSWORD="${DB_PASSWORD:-SeoMsLocal_2026_a1b2}"
  export DB_NAME="${DB_NAME:-seo_platform}"
  export DATABASE_URL="postgresql+psycopg2://${DB_USER}:${DB_PASSWORD}@${DB_HOST}:${DB_PORT}/${DB_NAME}"
fi

export REDIS_HOST="127.0.0.1"
export REDIS_PORT="6379"
export REDIS_DB="${REDIS_DB:-0}"
export REDIS_PASSWORD="${REDIS_PASSWORD:-SeoRedisMs_2026_c3d4}"
export CELERY_BROKER_URL="redis://:${REDIS_PASSWORD}@${REDIS_HOST}:${REDIS_PORT}/1"
export CELERY_RESULT_BACKEND="redis://:${REDIS_PASSWORD}@${REDIS_HOST}:${REDIS_PORT}/2"

# 生产环境 CORS 不允许 *，部署前端后请把前端域名加进来（逗号分隔）
export CORS_ORIGINS="${CORS_ORIGINS:-https://www.modelscope.cn}"
export FRONTEND_URL="${FRONTEND_URL:-https://www.modelscope.cn}"

# ---------- 2. PostgreSQL（外部数据库时跳过）----------
if [ "${IS_EXTERNAL_DB}" = "0" ]; then
  log "启动 PostgreSQL ${PG_VERSION}/${PG_CLUSTER} ..."
  if [ ! -d "/etc/postgresql/${PG_VERSION}/${PG_CLUSTER}" ]; then
    pg_createcluster "${PG_VERSION}" "${PG_CLUSTER}" >/dev/null
  fi
  if ! grep -q "timescaledb" "${PG_CONF}"; then
    echo "shared_preload_libraries = 'timescaledb'" >> "${PG_CONF}"
  fi
  pg_ctlcluster "${PG_VERSION}" "${PG_CLUSTER}" start || true

  for _ in $(seq 1 30); do
    if su postgres -c "pg_isready -q"; then break; fi
    sleep 1
  done
  su postgres -c "pg_isready -q" || { log "PostgreSQL 启动失败"; exit 1; }

  # 建角色（超级用户，TimescaleDB 建扩展/hypertable 需要）与数据库，幂等
  if ! su postgres -c "psql -tAc \"SELECT 1 FROM pg_roles WHERE rolname='${DB_USER}'\"" | grep -q 1; then
    log "创建数据库角色 ${DB_USER} ..."
    su postgres -c "psql -c \"CREATE ROLE ${DB_USER} LOGIN SUPERUSER PASSWORD '${DB_PASSWORD}'\""
  fi
  if ! su postgres -c "psql -tAc \"SELECT 1 FROM pg_database WHERE datname='${DB_NAME}'\"" | grep -q 1; then
    log "创建数据库 ${DB_NAME} ..."
    su postgres -c "createdb -O ${DB_USER} ${DB_NAME}"
  fi
fi

# ---------- 3. Redis ----------
log "启动 Redis ..."
redis-server --requirepass "${REDIS_PASSWORD}" --appendonly yes --dir "${DATA_DIR}" \
  >> "${DATA_DIR}/redis.log" 2>&1 &
for _ in $(seq 1 30); do
  if redis-cli -a "${REDIS_PASSWORD}" --no-auth-warning ping 2>/dev/null | grep -q PONG; then break; fi
  sleep 1
done

# ---------- 4. 数据库迁移 ----------
cd "${APP_DIR}"
log "执行数据库迁移 alembic upgrade head ..."
alembic upgrade head

# ---------- 5. Celery ----------
log "启动 Celery worker / beat ..."
# 监督循环：worker/beat 一旦退出（崩溃/OOM/异常）5 秒后自动拉起。
# 单容器部署没有外部进程管理器，裸 & 启动的子进程死亡后无人恢复——
# 表现为排期照常生成、到期动作永远不执行（执行链路静默停摆）。
# 输出不重定向，保持容器 stdout 可见（魔搭日志页）。
(
  while true; do
    celery -A app.core.celery_app worker -Q default,serp,social --loglevel=INFO --concurrency=2
    echo "[start] Celery worker exited, restarting in 5s ..."
    sleep 5
  done
) &
(
  while true; do
    celery -A app.core.celery_app beat --loglevel=INFO
    echo "[start] Celery beat exited, restarting in 5s ..."
    sleep 5
  done
) &

# ---------- 6. FastAPI（前台，创空间要求 7860）----------
log "启动 FastAPI，监听 0.0.0.0:7860 ..."
exec uvicorn app.main:app --host 0.0.0.0 --port 7860 \
  --proxy-headers --forwarded-allow-ips '*'
