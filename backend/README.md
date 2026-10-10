---
# 详细文档见 https://modelscope.cn/docs/studios
domain: nlp
tags:
  - seo
  - fastapi
datasets:
  evaluation:
  test:
  train:
models:
license: Apache License 2.0
---

# SEO Platform 后端（魔搭创空间 · Docker）

FastAPI + Celery 后端服务，单容器内自带 PostgreSQL 15（TimescaleDB）与 Redis，
镜像与启动流程见仓库根目录的 `Dockerfile` 与 `start.sh`。

## 部署形态

- `ms_deploy.json`：`sdk_type = docker`，服务端口 `7860`（创空间硬性要求）。
- 仓库根目录的 `Dockerfile` 为「创空间单容器全家桶」镜像：
  PostgreSQL 15 + TimescaleDB + Redis + Celery(worker/beat) + FastAPI。
- 交换端口：`0.0.0.0:7860`；健康检查：`GET /api/health`。

## 环境变量

在创空间「设置 → 环境变量」（或 `ms_deploy.json` 的 `environment_variables`）中配置：

| 变量 | 说明 |
| --- | --- |
| `DEEPSEEK_API_KEY` | DeepSeek 密钥，AI 生成/检测功能必需 |
| `APP_ENV` | 默认 `production`；生产会做弱口令/CORS 校验 |
| `CORS_ORIGINS` | 前端域名白名单，逗号分隔（生产禁止 `*`） |
| `SECRET_KEY` | 留空则启动时随机生成并持久化到容器数据目录 |
| `SCRAPINGBEE_API_KEY` | 可选，SERP 抓取 |
| `ZERNIO_API_BASE` | 可选，Reddit 账号/发帖网关 |

## 数据持久化

容器内数据库数据默认落在容器文件系统，重启/重建后重置（每次启动自动跑 `alembic upgrade head`）。
如需持久化，请将数据库目录挂载到 `/mnt/workspace`。

## 本地验证

```bash
docker build -t seo-backend-ms ./backend
docker run --rm -p 7860:7860 -e DEEPSEEK_API_KEY=xxx seo-backend-ms
curl http://127.0.0.1:7860/api/health
```

> `docker-compose.prod.yml` 使用的精简镜像为 `Dockerfile.api`，与本文件所述创空间镜像相互独立。
