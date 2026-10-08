# SEO Platform

面向单站 / 多站的 SEO 运营平台：**数据监控 · Reddit 社交分发 · 排名监控 · 运维看板**。

登录后自动关联默认工作空间；关键词、排名、Zernio Key、Reddit 账号与审核队列按站点隔离，可在顶栏切换当前站点。

## 功能概览

| 能力 | 说明 |
|------|------|
| 排名监控 | 关键词库 + Google SERP 抓取（ScrapingBee / 代理 / Mock），TimescaleDB 存趋势 |
| Reddit 运营 | Zernio 接账号；DeepSeek 生成帖/评；审核后发布；矩阵、社区、品牌/产品词库 |
| 数据监控 | 关键词 Top10、Reddit 发帖/评论统计、排名趋势 |
| 运维看板 | Celery 队列积压、SERP 配额与成本估算、任务台账 |

## 技术栈

| 层 | 技术 |
|----|------|
| 前端 | Vue 3 · Vite · TypeScript · Element Plus · Pinia · ECharts |
| 后端 | FastAPI · SQLAlchemy · Alembic · Celery |
| 数据 | PostgreSQL + TimescaleDB · Redis |
| 外部 | ScrapingBee（SERP）· Zernio（Reddit）· DeepSeek（文案） |

```
浏览器 Vue3 (:5173)  ── Bearer Token ──►  FastAPI (:8000) /api/v1
                                            ├── PostgreSQL + TimescaleDB
                                            ├── Redis（业务 + Celery）
                                            └── Celery Worker / Beat
```

## 快速开始

### 前置要求

- Python 3.11+（推荐 Conda 环境 `SEO`）
- Node.js 18+
- Docker（PostgreSQL + Redis）

### 1. 启动基础设施

```bash
docker compose up -d
```

本地端口（经 `docker-compose.override.yml`）：`127.0.0.1:5432`、`127.0.0.1:6379`。

### 2. 后端配置与迁移

```bash
cd backend
cp .env.example .env   # Windows: copy .env.example .env
# 编辑 .env：SECRET_KEY、DEEPSEEK_API_KEY、ALLOW_REGISTRATION 等
alembic upgrade head
```

### 3. 启动服务

**API**

```bash
cd backend
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

**Celery Worker**（新终端）

```bash
cd backend
celery -A app.core.celery_app worker --loglevel=info --pool=solo -Q default,serp,social
```

**Celery Beat**（新终端，定时任务）

```bash
cd backend
celery -A app.core.celery_app beat --loglevel=info
```

**前端**

```bash
cd frontend
npm install
npm run dev
```

访问：

- 前端：http://127.0.0.1:5173
- API 文档：http://127.0.0.1:8000/docs
- 健康检查：http://127.0.0.1:8000/api/health

本地开发可将 `ALLOW_REGISTRATION=true` 写入 `backend/.env` 以便自助注册。

## 仓库结构

```
SEO/
├── backend/          # FastAPI + Celery + Alembic
├── frontend/         # Vue3 管理台
├── docker-compose*.yml
├── 使用手册.md       # 完整功能与运维说明
└── README.md
```

## 文档

更完整的环境搭建、页面说明、工作流、API 与排错，见 **[使用手册.md](./使用手册.md)**。
