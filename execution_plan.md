# SEO 闭环系统 — 可执行计划书

> 基于 [project.md](file:///c:/SEO/project.md) 架构方案制定
> 建议总周期：**12 周（MVP）→ 持续迭代**

---

## 一、 项目阶段总览

| 阶段 | 周次 | 交付物 | 验收标准 |
|------|------|--------|----------|
| **P0 基础设施搭建** | Week 1-2 | 项目脚手架、数据库、Celery 队列、基础配置 | 能启动 FastAPI + PostgreSQL + Redis，API 健康检查通过 |
| **P1 内容创作引擎** | Week 3-4 | AI 辅写、SEO 检查器、文章管理 CRUD | 输入关键词能生成初稿，SEO 检查输出得分与建议 |
| **P2 推广分发引擎** | Week 5-6 | PulseForge 适配层、发帖调度、定时发布队列 | 文章发布后自动推送到 PulseForge，支持定时和差异化文案 |
| **P3 监控爬虫引擎** | Week 7-9 | SERP 排名抓取、爬虫日志、外链/竞品监控 | 关键词每日抓取排名，数据写入 TimescaleDB 时序表 |
| **P4 数据中台大屏** | Week 10-11 | 看板 Dashboard、策略推荐工单、图表可视化 | 能展示"发布→引流→排名变化"全链路闭环数据 |
| **P5 联调与上线** | Week 12 | 集成测试、部署文档、生产环境配置 | 全流程走通：规划→创作→推送→监控→反馈 |

---

## 二、 技术栈落地配置

### 2.1 核心依赖（Python）

```text
fastapi>=0.110          # API 框架
uvicorn[standard]       # ASGI 服务器
sqlalchemy>=2.0         # ORM
alembic                 # 数据库迁移
asyncpg                 # PostgreSQL 驱动
psycopg2-binary         # PostgreSQL 备选驱动
celery>=5.3             # 异步任务队列
redis>=5.0              # Broker + Cache
scrapy>=2.11            # 爬虫框架
playwright>=1.40        # JS 渲染爬虫
httpx>=0.27             # HTTP 客户端（异步）
pydantic>=2.5           # 数据校验
pydantic-settings       # 配置管理
python-dotenv           # .env 加载
python-multipart        # 文件上传
jinja2                  # 模板（如需要）
timescaledb-sqlalchemy  # TimescaleDB 支持
deepseek-api            # DeepSeek AI 客户端（或直接 httpx 调用）
python-jose[cryptography]  # JWT
passlib[bcrypt]         # 密码哈希
echarts-python 或 纯前端 ECharts  # 图表
```

### 2.2 前端（管理后台）

```text
Vue 3 + Vite + TypeScript
Pinia（状态管理）
Vue Router
Element Plus（UI 组件库）
ECharts 5（图表可视化）
Axios（HTTP）
```

### 2.3 外部服务

| 服务 | 用途 | 选型建议 |
|------|------|----------|
| **大语言模型** | AI 内容生成 | **DeepSeek API**（用户约束要求） |
| **住宅代理** | SERP 抓取防封 | BrightData / Smartproxy（付费，按流量计费） |
| **对象存储** | 文章封面、爬取截图 | 阿里云 OSS / 七牛云 / 本地 MinIO |
| **SMTP/邮件** | 告警通知 | SendGrid / 阿里云邮件推送 |

### 2.4 开发环境（Docker Compose 一键启动）

```yaml
# docker-compose.yml 核心服务
services:
  postgres:
    image: timescale/timescaledb:latest-pg16
    ports: ["5432:5432"]
    environment:
      POSTGRES_DB: seo_platform
      POSTGRES_USER: seo
      POSTGRES_PASSWORD: seo_dev_pass
    volumes:
      - pgdata:/var/lib/postgresql/data

  redis:
    image: redis:7-alpine
    ports: ["6379:6379"]
    command: redis-server --appendonly yes

  celery-worker:
    build: .
    command: celery -A app.core.celery_app worker --loglevel=info -Q default,serp,social

  celery-beat:
    build: .
    command: celery -A app.core.celery_app beat --loglevel=info
```

---

## 三、 项目目录结构

```
c:\SEO\
├── backend/                          # Python FastAPI 后端
│   ├── app/
│   │   ├── main.py                   # FastAPI 入口
│   │   ├── api/                      # 路由层
│   │   │   ├── v1/
│   │   │   │   ├── auth.py           # 登录/注册
│   │   │   │   ├── articles.py       # 文章 CRUD
│   │   │   │   ├── seo_checker.py    # SEO 检查 API
│   │   │   │   ├── ai_writer.py      # AI 写作 API
│   │   │   │   ├── social.py         # PulseForge / 社交分发
│   │   │   │   ├── serp.py           # 排名监控 API
│   │   │   │   ├── crawler_logs.py   # 爬虫日志 API
│   │   │   │   ├── backlinks.py      # 外链监控 API
│   │   │   │   ├── competitors.py    # 竞品对标 API
│   │   │   │   └── dashboard.py      # 大屏数据 API
│   │   ├── core/                     # 核心配置
│   │   │   ├── config.py             # 设置（pydantic-settings）
│   │   │   ├── security.py           # JWT / 密码
│   │   │   ├── celery_app.py         # Celery 实例
│   │   │   └── database.py           # SQLAlchemy 会话
│   │   ├── models/                   # SQLAlchemy 模型
│   │   │   ├── user.py
│   │   │   ├── article.py
│   │   │   ├── keyword.py
│   │   │   ├── serp_rank.py          # 时序模型
│   │   │   ├── social_post.py
│   │   │   ├── crawler_log.py
│   │   │   ├── backlink.py
│   │   │   └── competitor.py
│   │   ├── schemas/                  # Pydantic DTO
│   │   ├── services/                 # 业务逻辑层
│   │   │   ├── ai_writer.py          # DeepSeek API 封装
│   │   │   ├── seo_analyzer.py       # SEO 检查器（标题/H/ALT/内链）
│   │   │   ├── pulseforge_client.py  # PulseForge API 适配层
│   │   │   ├── serp_crawler.py       # Google 排名抓取
│   │   │   ├── crawler_monitor.py    # 搜索引擎蜘蛛日志分析
│   │   │   ├── backlink_monitor.py   # 外链监控
│   │   │   └── recommender.py        # 自动策略推荐
│   │   ├── tasks/                    # Celery 异步任务
│   │   │   ├── ai_tasks.py
│   │   │   ├── social_tasks.py       # 定时发帖
│   │   │   ├── serp_tasks.py         # 定时爬排名
│   │   │   └── alert_tasks.py        # 告警通知
│   │   ├── utils/                    # 工具函数
│   │   │   ├── proxy.py              # 代理池管理
│   │   │   ├── rate_limiter.py       # 限流器 / 熔断
│   │   │   └── text.py               # 文本处理（关键词密度等）
│   │   └── templates/                # 邮件模板等
│   ├── alembic/                      # 数据库迁移
│   ├── tests/                        # 测试（pytest）
│   ├── requirements.txt
│   ├── Dockerfile
│   └── .env.example
│
├── frontend/                         # Vue3 管理后台
│   ├── src/
│   │   ├── views/
│   │   │   ├── ArticleEditor.vue     # 文章编辑器（Markdown/WYSIWYG）
│   │   │   ├── SeoChecker.vue        # SEO 得分面板
│   │   │   ├── SocialScheduler.vue   # 社交发帖调度
│   │   │   ├── SerpMonitor.vue       # 排名趋势图
│   │   │   ├── CrawlerLogs.vue       # 爬虫日志
│   │   │   ├── Backlinks.vue         # 外链监控
│   │   │   ├── Competitors.vue       # 竞品对标
│   │   │   ├── Dashboard.vue         # 综合大屏
│   │   │   └── Recommendations.vue   # 优化建议工单
│   │   ├── api/                      # Axios 封装
│   │   ├── stores/                   # Pinia
│   │   └── router/
│   └── package.json
│
├── docker-compose.yml
└── execution_plan.md                 # 本文件
```

---

## 四、 数据库 Schema 设计

### 4.1 核心业务表（PostgreSQL）

```sql
-- 用户表
CREATE TABLE users (
    id SERIAL PRIMARY KEY,
    email VARCHAR(255) UNIQUE NOT NULL,
    hashed_password VARCHAR(255) NOT NULL,
    full_name VARCHAR(100),
    is_active BOOLEAN DEFAULT true,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- 关键词库
CREATE TABLE keywords (
    id SERIAL PRIMARY KEY,
    user_id INT REFERENCES users(id),
    keyword VARCHAR(200) NOT NULL,
    target_url VARCHAR(500),
    search_engine VARCHAR(20) DEFAULT 'google',  -- google / bing / baidu
    region VARCHAR(10) DEFAULT 'us',              -- us / cn / global
    priority INT DEFAULT 3,                       -- 1=最高
    status VARCHAR(20) DEFAULT 'active',
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX idx_keywords_user ON keywords(user_id);

-- 文章表
CREATE TABLE articles (
    id SERIAL PRIMARY KEY,
    user_id INT REFERENCES users(id),
    keyword_id INT REFERENCES keywords(id),
    title VARCHAR(300) NOT NULL,
    meta_description VARCHAR(320),
    slug VARCHAR(300) UNIQUE,
    content TEXT,                                -- Markdown 正文
    cover_image_url VARCHAR(500),
    status VARCHAR(20) DEFAULT 'draft',          -- draft / ai_generated / reviewed / published
    seo_score DECIMAL(5,2),                      -- 0-100
    published_at TIMESTAMPTZ,
    target_url VARCHAR(500),                     -- 实际发布的外部/内部 URL
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX idx_articles_user_status ON articles(user_id, status);

-- 内链推荐记录
CREATE TABLE internal_links (
    id SERIAL PRIMARY KEY,
    source_article_id INT REFERENCES articles(id),
    target_article_id INT REFERENCES articles(id),
    anchor_text VARCHAR(200),
    is_applied BOOLEAN DEFAULT false,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- 社交账号配置
CREATE TABLE social_accounts (
    id SERIAL PRIMARY KEY,
    user_id INT REFERENCES users(id),
    platform VARCHAR(50) NOT NULL,               -- pulseforge / linkedin / twitter / facebook
    account_name VARCHAR(100),
    access_token TEXT,
    refresh_token TEXT,
    token_expires_at TIMESTAMPTZ,
    config JSONB,                                 -- 平台特定配置
    is_active BOOLEAN DEFAULT true,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- 社交发帖任务
CREATE TABLE social_posts (
    id SERIAL PRIMARY KEY,
    article_id INT REFERENCES articles(id),
    account_id INT REFERENCES social_accounts(id),
    title VARCHAR(300),                          -- 差异化文案标题
    summary TEXT,                                -- 摘要/正文
    image_url VARCHAR(500),
    external_url VARCHAR(500),                   -- 文章链接
    scheduled_at TIMESTAMPTZ,
    posted_at TIMESTAMPTZ,
    platform_post_id VARCHAR(200),               -- 平台返回的 ID
    status VARCHAR(20) DEFAULT 'pending',        -- pending / scheduled / posted / failed
    engagement JSONB,                            -- 点赞、评论、分享数
    error_message TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX idx_social_posts_status ON social_posts(status, scheduled_at);

-- 外链表
CREATE TABLE backlinks (
    id SERIAL PRIMARY KEY,
    user_id INT REFERENCES users(id),
    target_url VARCHAR(500) NOT NULL,
    source_url VARCHAR(500) NOT NULL,
    anchor_text VARCHAR(300),
    domain_authority INT,
    is_alive BOOLEAN DEFAULT true,
    first_seen_at TIMESTAMPTZ DEFAULT NOW(),
    last_checked_at TIMESTAMPTZ,
    lost_at TIMESTAMPTZ
);
CREATE UNIQUE INDEX idx_backlinks_unique ON backlinks(target_url, source_url);

-- 竞品表
CREATE TABLE competitors (
    id SERIAL PRIMARY KEY,
    user_id INT REFERENCES users(id),
    domain VARCHAR(200) NOT NULL,
    name VARCHAR(100),
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- 搜索引擎蜘蛛抓取日志
CREATE TABLE crawler_logs (
    id BIGSERIAL PRIMARY KEY,
    user_id INT REFERENCES users(id),
    url_path VARCHAR(500) NOT NULL,
    crawler_name VARCHAR(50) NOT NULL,           -- Googlebot / Bingbot / Baiduspider
    status_code INT NOT NULL,
    user_agent TEXT,
    ip INET,
    crawled_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX idx_crawler_logs_time ON crawler_logs(crawled_at DESC);
CREATE INDEX idx_crawler_logs_crawler ON crawler_logs(crawler_name, crawled_at);

-- 优化建议工单
CREATE TABLE recommendations (
    id SERIAL PRIMARY KEY,
    user_id INT REFERENCES users(id),
    article_id INT REFERENCES articles(id),
    keyword_id INT REFERENCES keywords(id),
    category VARCHAR(50) NOT NULL,               -- content / performance / social / link
    severity VARCHAR(20) DEFAULT 'info',         -- info / warning / critical
    title VARCHAR(300) NOT NULL,
    description TEXT,
    suggestion TEXT,                             -- 具体怎么做
    status VARCHAR(20) DEFAULT 'open',           -- open / in_progress / resolved / ignored
    created_at TIMESTAMPTZ DEFAULT NOW(),
    resolved_at TIMESTAMPTZ
);
```

### 4.2 时序数据表（TimescaleDB 扩展，按时间自动分区）

```sql
-- 启用 TimescaleDB 扩展
CREATE EXTENSION IF NOT EXISTS timescaledb;

-- SERP 排名快照
CREATE TABLE serp_rank_snapshots (
    time TIMESTAMPTZ NOT NULL,
    keyword_id INT NOT NULL REFERENCES keywords(id),
    target_url VARCHAR(500),
    rank INT,
    page INT,
    serp_features JSONB,                         -- Knowledge Panel / People Also Ask 等
    search_region VARCHAR(10),
    proxy_used VARCHAR(100),
    crawl_status VARCHAR(20) DEFAULT 'success',  -- success / blocked / timeout
    error_message TEXT
);
-- 转为 Hypertable（自动按天分块）
SELECT create_hypertable('serp_rank_snapshots', 'time');
CREATE INDEX idx_serp_keyword_time ON serp_rank_snapshots(keyword_id, time DESC);

-- 竞品排名快照
CREATE TABLE competitor_rank_snapshots (
    time TIMESTAMPTZ NOT NULL,
    competitor_id INT NOT NULL REFERENCES competitors(id),
    keyword_id INT NOT NULL REFERENCES keywords(id),
    domain VARCHAR(200),
    rank INT,
    target_url VARCHAR(500)
);
SELECT create_hypertable('competitor_rank_snapshots', 'time');

-- 社交互动数据时序
CREATE TABLE social_engagement_snapshots (
    time TIMESTAMPTZ NOT NULL,
    social_post_id INT NOT NULL REFERENCES social_posts(id),
    platform VARCHAR(50),
    likes INT DEFAULT 0,
    comments INT DEFAULT 0,
    shares INT DEFAULT 0,
    clicks INT DEFAULT 0
);
SELECT create_hypertable('social_engagement_snapshots', 'time');

-- 性能指标（Core Web Vitals 预估/实测）
CREATE TABLE web_vitals_snapshots (
    time TIMESTAMPTZ NOT NULL,
    article_id INT REFERENCES articles(id),
    url VARCHAR(500) NOT NULL,
    lcp DECIMAL(8,2),    -- Largest Contentful Paint (ms)
    cls DECIMAL(6,4),    -- Cumulative Layout Shift
    fid DECIMAL(8,2),    -- First Input Delay (ms) → 被 INP 替代
    inp DECIMAL(8,2),    -- Interaction to Next Paint (ms)
    ttfb DECIMAL(8,2)    -- Time To First Byte (ms)
);
SELECT create_hypertable('web_vitals_snapshots', 'time');
```

---

## 五、 分阶段可执行任务清单

### P0：基础设施搭建（Week 1-2）

**目标**：跑通脚手架，开发环境一键启动。

| # | 任务 | 具体动作 | 产出 |
|---|------|----------|------|
| P0.1 | 初始化后端项目 | 创建 `backend/`，`pip install` 依赖，写 `main.py` + 健康检查 `/api/health` | 启动后 `GET /api/health` 返回 `{"status":"ok"}` |
| P0.2 | 配置 Docker Compose | 编写 `docker-compose.yml`（pg + timescale 扩展 + redis） | `docker compose up` 能启动 3 个服务 |
| P0.3 | 数据库接入 | `core/database.py` 配置 SQLAlchemy 引擎 + SessionLocal；创建 `models/base.py` | 能连上 PostgreSQL |
| P0.4 | 用户认证 | 实现 `/auth/register`、`/auth/login`（JWT），`users` 表 + Alembic 初始迁移 | 能注册登录，受保护路由返回 401 |
| P0.5 | Celery 集成 | `core/celery_app.py` 配置 broker=redis；写一个 demo task `add(x,y)` | Celery worker 能消费任务 |
| P0.6 | 前端脚手架 | Vite + Vue3 + Element Plus + Pinia + Vue Router；登录页 + 布局框架 | 浏览器能打开后台，登录后跳首页 |
| P0.7 | 配置管理 | `.env.example` + `core/config.py`（DB URL、Redis、DeepSeek Key、代理密钥） | 所有密钥不硬编码 |

**验收命令**：
```bash
# 后端
cd backend && python -m uvicorn app.main:app --reload
# → http://127.0.0.1:8000/api/health 返回 ok

# Celery
celery -A app.core.celery_app worker --loglevel=info
# → 能看到 task 注册日志

# Alembic
alembic upgrade head
# → 所有基础表创建成功
```

---

### P1：内容创作与优化引擎（Week 3-4）

**目标**：输入关键词 → AI 生成文章 → SEO 打分与建议。

| # | 任务 | 具体动作 | 产出 |
|---|------|----------|------|
| P1.1 | DeepSeek AI 写作封装 | `services/ai_writer.py`：调用 DeepSeek Chat Completions；Prompt 模板："你是SEO专家，围绕 {keyword} 写一篇 1500 字长文，结构含 H1/H2/H3..." | 输入关键词返回 Markdown 正文 |
| P1.2 | 文章 CRUD API | `api/v1/articles.py`：增删改查；状态机 `draft → ai_generated → reviewed → published` | 前后端能保存/编辑文章 |
| P1.3 | Markdown 编辑器 | 前端 `ArticleEditor.vue`：集成 md-editor-v3 或类似 Markdown 编辑器，带 SEO 信息输入框（Title/Meta/封面） | 能编辑并实时预览 |
| P1.4 | SEO 检查器 - 基础指标 | `services/seo_analyzer.py`：① Title 长度 50-60 字符 ② Meta Description 150-160 ③ 关键词密度（正文 1-3%）④ H1 唯一且含关键词 | 返回分项得分 + 总分 0-100 |
| P1.5 | SEO 检查器 - 结构 & 内链 | ① H2/H3 层级合理（不跳级）② 正文图片 `<img>` 自动检测 alt 缺失 ③ 基于 TF-IDF/向量相似度从已有文章推荐 Top-5 内链锚文本 | 前端 `SeoChecker.vue` 显示清单 + 一键插入内链 |
| P1.6 | Core Web Vitals 预估 | 对 Markdown 转 HTML 后估算：① 首图大小→LCP 风险 ② 布局 shift 风险（无尺寸图片） ③ TTFB 基线（待 P3 接入真实 Lighthouse） | 纳入 SEO 总分权重 |
| P1.7 | AI 内容检测标记 | 调用 DeepSeek 自检或集成简单规则（重复句、AI 典型句式），给文章打 `ai_detected_score` 并提示人工修正工单 → 写入 `recommendations` | 避免纯 AI 低质量内容 |

**验收**：输入关键词"Python asyncio 最佳实践" → 10 秒内生成初稿 → SEO 检查输出 12 项结果 + 总分 → 一键保存文章。

---

### P2：推广分发与社交引擎（Week 5-6）

**目标**：文章发布时自动触达 PulseForge + 多平台定时发帖。

| # | 任务 | 具体动作 | 产出 |
|---|------|----------|------|
| P2.1 | PulseForge API 适配层 | `services/pulseforge_client.py`：定义统一接口 `class PulseForgeClient` 方法：`auth()`、`create_post()`、`schedule_post()`、`get_engagement()`；如无开放 API，先做 Playwright 模拟登录+发帖的 fallback，并标记为"不稳定模式" | 封装 4 个基础方法 + 单元测试 mock |
| P2.2 | 社交账号配置 UI | 前端 `SocialScheduler.vue`：新增 PulseForge / LinkedIn / Twitter 账号（输入 Token 或引导 OAuth） | 数据写入 `social_accounts` |
| P2.3 | 差异化文案生成 | 文章发布时，AI 基于正文生成 N 条不同平台的摘要：LinkedIn 偏专业长文、Twitter 偏短句+hashtag、Facebook 偏互动提问 | 每条摘要可人工编辑后保存 |
| P2.4 | 发帖调度队列 | Celery task `social_tasks.send_post(account_id, payload)`，参数含 `eta=scheduled_at`；带重试 3 次 + 指数退避 | 能"立即发"和"定时发" |
| P2.5 | 联动触发 | `articles` 状态变为 `published` 时，自动：① 提取摘要+封面→② 为每个激活账号生成 `social_posts` 记录→③ 默认 30 分钟后陆续发送（错峰） | 发布文章后调度面板自动出现待发任务 |
| P2.6 | 限流熔断 | `utils/rate_limiter.py`：基于 Token Bucket，每平台独立 QPS 限制；失败率 > 50% 时自动熔断 1 小时；Redis 存计数 | API 调用日志能看到限流拦截 |
| P2.7 | 社区互动池草稿 | 针对新文章，AI 生成 3 条"像真人"的评论草稿模板（例如"补充一点我在项目中的经验是..."），写入待人工审核 | 前端 Review 列表 + 一键复制 |

**验收**：发布一篇文章 → 3 个平台各生成 1 条待发 → 手动触发立即发送 → `social_posts.status = 'posted'` 并记录 `platform_post_id`。

---

### P3：实时监控与爬虫引擎（Week 7-9，3 周重点）

**目标**：排名每 6 小时刷新；爬虫蜘蛛日志入库；外链/竞品同步。

| # | 任务 | 具体动作 | 产出 |
|---|------|----------|------|
| P3.1 | 代理池管理 | `utils/proxy.py`：加载 BrightData/Smartproxy 的代理列表（账号密码格式）；随机轮换；健康检测（请求 httpbin 延迟 < 3s）；失败自动剔除 | 爬虫请求前自动取代理 |
| P3.2 | Google SERP 爬虫 | `services/serp_crawler.py`：Playwright 或 httpx + 代理 访问 `https://www.google.com/search?q={kw}&gl={region}&num=100`；解析 organic 结果（标题/URL/位置）；处理验证码（抛出 BLOCKED 状态，切换代理重试） | 输入关键词返回前 100 名 URL 列表 |
| P3.3 | SERP 定时任务 | Celery Beat 每 6 小时触发：扫描 `keywords` 表 `status=active` → 每条投递 `serp_tasks.crawl_keyword_rank` → 写 `serp_rank_snapshots` | 时序表有持续新增数据 |
| P3.4 | 蜘蛛爬虫日志接入 | 两种方式二选一：① Nginx/Apache access.log 每日离线解析脚本 `scripts/parse_access_log.py` ② 站点埋点（推荐，middleware 识别 UA）：`Googlebot/2.1`、`Bingbot`、`Baiduspider` → 写入 `crawler_logs` | Dashboard 能看到蜘蛛访问趋势 |
| P3.5 | 抓取异常告警 | Celery 每日统计：对比前 7 天日均抓取量，若今日 < 50% → 生成 `recommendations`（severity=critical）+ 邮件告警；404/503 突增同理 | 异常 10 分钟内收到通知 |
| P3.6 | 外链监控 | `services/backlink_monitor.py`：① 对接 Ahrefs/Majestic API（付费）或 ② 自建：每月一轮 Google `link:yoursite.com` 抓取 + `HEAD` 请求验证存活；新发现→入库，丢失→`lost_at` 标红 | 外链新增/丢失列表 |
| P3.7 | 竞品对标抓取 | 每次 SERP 抓取同时记录竞品域名的排名；`competitor_rank_snapshots` 入库 | 前端 `Competitors.vue` 同台对比折线图 |
| P3.8 | Playwright JS 渲染爬虫（备选） | 针对 SPA 站点的对标页面分析；可选，优先完成 P3.2 | 可抓取 JS 渲染后内容 |

**验收**：添加 10 个关键词 → 24 小时后 `serp_rank_snapshots` 有 40 条记录 × 排名字段完整；蜘蛛日志持续写入；竞品同关键词排名能对比。

---

### P4：数据中台与决策大屏（Week 10-11）

**目标**：闭环可视化 + 自动策略推荐。

| # | 任务 | 具体动作 | 产出 |
|---|------|----------|------|
| P4.1 | 大屏骨架 | `Dashboard.vue`：6 个核心卡片（本周新发布文章数、总关键词 Top10 占比、PulseForge 引流点击、蜘蛛抓取次数、外链净增、待处理建议数）+ 4 张图表 | 进入即看到全景 |
| P4.2 | 排名趋势图 | ECharts Line：X=时间，Y=排名（倒序，越小越好），支持多关键词叠加；数据源：`serp_rank_snapshots` 按 `time` 聚合 | 能选关键词、缩放日期范围 |
| P4.3 | 社交引流漏斗图 | Funnel 图：文章发布 → 社交曝光 → 点击进站 → 停留；点击数可接入 GA4 API 或 UTM 参数统计 | 显示哪条文案点击率最高 |
| P4.4 | 策略推荐引擎（规则 V1） | `services/recommender.py` 规则：<br>① 跳出率 > 80%（需 GA 数据）→ "优化首段 3 句话"<br>② 发布 7 天排名 > 50 → "PulseForge 再推一次 + 追加 Reddit 互动"<br>③ LCP > 2.5s → "压缩首屏图片至 < 200KB"<br>④ 社交 0 互动 → "修改配图 / 标题加悬念"<br>⑤ H2 不含关键词 → "在 H2 中自然插入 {keyword}" | 规则命中即写入 `recommendations` 表 |
| P4.5 | 工单面板 UI | `Recommendations.vue`：按严重程度排序；支持标记 resolved / ignored；resolved 打时间戳 | 类似 Trello 卡片 |
| P4.6 | 发布 24h 报告 | 文章发布 24h 后自动生成一份聚合报告：SERP 初始排名、社交互动量、蜘蛛抓取次数、内链数、SEO 得分变化 → 邮件/站内信 | 用户收到闭环反馈 |

**验收**：打开大屏 → 数据全部来自真实接口（非 mock）；模拟"发布 7 天排名 80 名"→ 自动出现 P4.4 第 ② 条建议。

---

### P5：联调与上线（Week 12）

| # | 任务 | 具体动作 |
|---|------|----------|
| P5.1 | 全流程 E2E 测试 | 走一遍完整飞轮：输入关键词 → AI 写文 → SEO 检查改分 → 发布 → PulseForge 发帖 → 24h 后 SERP 抓取 → 大屏显示 → 策略建议生成 |
| P5.2 | 压力测试 | 100 关键词并发爬 SERP → 观察代理池、Celery 队列积压、Redis 内存、PG 连接数 |
| P5.3 | 生产环境配置 | Nginx 反向代理、HTTPS 证书、Gunicorn 多 worker、Celery 多队列（default/serp/social 分离）、日志轮转 |
| P5.4 | 监控告警 | Prometheus + Grafana（可选）或 Sentry 接入；关键指标：API 5xx 率、Celery 死信队列、SERP 封锁率 |
| P5.5 | 部署文档 | `docs/deploy.md`：从 0 到 1 启动生产环境的每一步命令 |

---

## 六、 关键风险与应对

| 风险 | 概率 | 影响 | 应对策略 |
|------|------|------|----------|
| **Google 封代理 IP** | 高 | SERP 数据断档 | ① 必须用付费住宅代理，预算 ¥300-1000/月 ② 请求间随机 sleep 2-8s ③ User-Agent 轮换 ④ 被封立刻换代理 + 该关键词延迟 24h 再试 |
| **PulseForge 无 API 或限流** | 中 | 社交分发中断 | ① P2.1 先调研 API 文档，若无则 Playwright 自动化 + Cookie 池 ② 限流 1 次/分钟/账号 ③ 失败不丢任务，入死信队列人工介入 |
| **AI 内容被搜素引擎判定低质量** | 高 | SEO 效果为 0 | ① P1.7 强制 AI 检测 + 人工修稿工单 ② 加 E-E-A-T 要素：作者简介、引用来源、真实案例、数据图表 ③ 禁止一次批量发布 50 篇，日均 ≤ 3 篇 |
| **Celery 任务积压** | 中 | 延迟大 | ① 队列拆分：serp、social、default 各自独立 worker ② Flower 面板监控 ③ worker 自动扩容（K8s HPA 或 supervisor 多进程） |
| **时序数据膨胀** | 低 | DB 磁盘满 | ① TimescaleDB 自动设置 90 天数据降采样（aggregate view）② 冷数据归档到对象存储 ③ 索引定期维护 |

---

## 七、 每日开发命令速查

```powershell
# 0. 启动所有依赖（首次执行）
cd c:\SEO
docker compose up -d

# 1. 后端开发
cd c:\SEO\backend
python -m venv .venv
.venv\Scripts\Activate.ps1          # PowerShell
pip install -r requirements.txt
alembic upgrade head                # 数据库迁移
python -m uvicorn app.main:app --reload --port 8000

# 2. Celery worker（新终端）
cd c:\SEO\backend
.venv\Scripts\Activate.ps1
celery -A app.core.celery_app worker -l info -Q default,serp,social --pool=solo
# （Windows 下推荐 --pool=solo 或 eventlet）

# 3. Celery Beat 定时调度（新终端）
celery -A app.core.celery_app beat -l info

# 4. 前端开发
cd c:\SEO\frontend
npm install
npm run dev                         # 默认 http://localhost:5173

# 5. 跑测试
cd c:\SEO\backend
pytest tests/ -v
```

---

## 八、 里程碑验收 CheckList

- [ ] **P0**：健康检查通过；用户注册登录；Celery 任务执行成功；前端后台可登录
- [ ] **P1**：关键词 → AI 出稿 → SEO 检查 → 保存文章 → 全链路无报错
- [ ] **P2**：PulseForge 至少 1 个平台成功发帖；定时发布准时触发；限流未击穿
- [ ] **P3**：24h 内 4 次 SERP 快照成功写入；蜘蛛日志有数据；外链有新增记录
- [ ] **P4**：大屏 6 卡 4 图全有真实数据；规则命中自动生成建议工单
- [ ] **P5**：E2E 全流程走通；部署文档完整可复现
