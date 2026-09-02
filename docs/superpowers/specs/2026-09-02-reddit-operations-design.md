# Reddit 运营模块设计规格

> **日期：** 2026-09-02  
> **状态：** 已批准  
> **范围：** Reddit 子版块发帖 + 评论，审核队列优先，预留全自动接口

---

## 1. 背景与目标

SEO Platform 已有社交分发模块（`/social`），支持多平台发帖调度与 AI 评论草稿生成，但 Reddit 客户端为 mock，评论仅生成文本、不能发布，且缺少「咨询帖」「经验分享帖」等内容形态。

本模块目标：

1. **Reddit OAuth 一键连接账号**，替代手动粘贴 Token。
2. **发帖审核队列**：支持咨询帖（A）、经验分享帖（B），AI 生成 → 人工预览/编辑 → 批准发布。
3. **评论审核队列**：支持手动粘贴帖子 URL、关键词搜索挑选目标帖，AI 生成评论 → 人工审核 → 批准发布。
4. **预留全自动接口**：搜索与入队 API 支持 `auto_discover` / `auto_queue` 参数，当前 UI 不启用，供后续 Celery 定时任务调用。

不在本期范围：文章引流帖（D）、纯产品清单帖（C）、非 Reddit 平台、全自动定时 UI。

---

## 2. 需求摘要

| 维度 | 决策 |
|------|------|
| 平台 | Reddit（首发） |
| 执行方式 | 审核队列（draft → pending_review → approved → posted） |
| 发帖类型 | `consultation`（咨询）、`experience`（经验分享） |
| 评论入口 | 手动 URL + 关键词搜索；预留 `auto_discover=true` |
| 站点 URL | 从当前用户默认 `client_sites.domain` 自动带入（可覆盖） |

---

## 3. 架构

```
浏览器 /social → Reddit 运营 Tab
    ├── OAuth 连接 Reddit
    ├── 发帖：生成 → 审核 → 发布
    └── 评论：URL/搜索 → 生成 → 审核 → 发布

FastAPI /api/v1/reddit/*
    ├── GET  /oauth/start
    ├── GET  /oauth/callback
    ├── GET  /status
    ├── POST /posts/generate
    ├── GET  /posts
    ├── PATCH /posts/{id}
    ├── POST /posts/{id}/approve
    ├── POST /posts/{id}/reject
    ├── POST /posts/{id}/publish
    ├── POST /comments/generate
    ├── GET  /comments
    ├── PATCH /comments/{id}
    ├── POST /comments/{id}/approve
    ├── POST /comments/{id}/reject
    ├── POST /comments/{id}/publish
    └── GET  /discover/search

Services
    ├── reddit_oauth.py      OAuth 会话（参照 gsc_client 模式）
    ├── reddit_client.py     Reddit API 封装（发帖/评论/搜索/读帖）
    └── ai_writer.py         新增 Reddit 帖/评论生成方法

Data
    ├── reddit_posts         发帖审核队列
    └── reddit_comments      评论审核队列

Token 存储：复用 social_accounts（platform=reddit），OAuth 完成后 upsert 记录。
```

---

## 4. 数据模型

### 4.1 `reddit_posts`

| 列 | 类型 | 说明 |
|----|------|------|
| id | int PK | |
| site_id | int FK | 站点隔离 |
| account_id | int FK | → social_accounts |
| post_type | varchar(20) | `consultation` / `experience` |
| subreddit | varchar(100) | 不含 `r/` 前缀 |
| keyword | varchar(200) | 生成主题词 |
| title | varchar(300) | 帖子标题 |
| body | text | 帖子正文 |
| site_url | varchar(500) nullable | 可选引流链接 |
| status | varchar(20) | 见状态机 |
| reddit_post_id | varchar(50) nullable | 发布后 `t3_xxx` |
| reddit_permalink | varchar(500) nullable | |
| error_message | text nullable | |
| created_at / updated_at | timestamptz | |

**状态机：** `draft` → `pending_review` → `approved` → `posting` → `posted` | `failed`；任意审核前状态可 → `rejected`。

### 4.2 `reddit_comments`

| 列 | 类型 | 说明 |
|----|------|------|
| id | int PK | |
| site_id | int FK | |
| account_id | int FK | |
| target_post_url | varchar(500) | 原始 URL |
| target_thing_id | varchar(20) | `t3_xxxx` |
| subreddit | varchar(100) | |
| target_post_title | varchar(500) nullable | 搜索/读帖缓存 |
| body | text | 评论正文 |
| keyword | varchar(200) nullable | 搜索用词 |
| discover_source | varchar(20) | `manual_url` / `keyword_search` / `auto_discover` |
| status | varchar(20) | 同发帖状态机 |
| reddit_comment_id | varchar(50) nullable | |
| error_message | text nullable | |
| created_at / updated_at | timestamptz | |

---

## 5. Reddit OAuth

### 5.1 环境变量

```env
REDDIT_CLIENT_ID=xxxxxxxx
REDDIT_CLIENT_SECRET=xxxxxxxx
REDDIT_REDIRECT_URI=http://127.0.0.1:8000/api/v1/reddit/oauth/callback
REDDIT_USER_AGENT=SEOPlatform/1.0 (by /u/your_reddit_username)
```

### 5.2 OAuth 流程

参照 `gsc_client.py`：

1. `GET /reddit/oauth/start` → 生成 state + PKCE（可选，Reddit 支持 confidential client 无 PKCE）→ 返回 Reddit 授权 URL。
2. Scope：`identity read submit`。
3. `GET /reddit/oauth/callback` → 换 token → 调 `/api/v1/me` 取 username → upsert `social_accounts`（platform=reddit, account_name=u/xxx）。
4. 回调后重定向前端 `/social?reddit=connected`。

### 5.3 Token 刷新

Reddit refresh_token 长期有效（duration=permanent）。发布前若 access_token 过期，用 refresh_token 刷新。

---

## 6. Reddit API 封装

`reddit_client.py` 职责：

| 方法 | Reddit API | 说明 |
|------|------------|------|
| `get_me()` | GET /api/v1/me | 验证连接 |
| `submit_post(subreddit, title, body)` | POST /api/submit | kind=self |
| `submit_comment(thing_id, body)` | POST /api/comment | |
| `get_post_context(thing_id)` | GET /comments/{id} | 读帖标题+正文供 AI |
| `search_posts(subreddit, keyword, limit)` | GET /r/{sr}/search | restrict_sr=1, sort=relevance |

**Mock 模式：** 未配置 `REDDIT_CLIENT_ID` 时，与 PulseForge 一致返回假 ID，便于本地开发。

**限流：** 复用 `rate_limiter` 中 reddit 配置（10 秒 1 次）+ `CircuitBreaker`。

---

## 7. AI 生成策略

### 7.1 咨询帖（consultation）

- 英文、第一人称、真实提问语气。
- 引发讨论，不含硬广、不含链接（`site_url` 忽略）。
- 长度：标题 ≤ 300 字符，正文 100–250 词。

示例方向：*"FTM looking for baby monitor recommendations — need something that works through thick walls. Budget ~$150. What do you use?"*

### 7.2 经验分享帖（experience）

- 英文、第一人称分享真实使用经历。
- 可自然提及 1–2 个产品名；若 `include_site_url=true`，文末软引站点链接。
- 长度：正文 150–350 词。

### 7.3 评论

- 读取目标帖标题+正文摘要（Reddit API）。
- 英文、像真人、有具体细节或追问。
- 经验分享型评论可在合适语境下软引 `site_url`（用户可选 `include_site_url`）。
- 长度：50–120 词。

---

## 8. API 规格

### 8.1 发帖

**POST `/reddit/posts/generate`**

```json
{
  "account_id": 1,
  "post_type": "consultation",
  "subreddit": "BabyBumps",
  "keyword": "baby monitor",
  "include_site_url": false
}
```

响应：`RedditPostOut`，status=`pending_review`。

**POST `/reddit/posts/{id}/approve`** — status → `approved`  
**POST `/reddit/posts/{id}/reject`** — status → `rejected`  
**POST `/reddit/posts/{id}/publish`** — 调 Reddit API，status → `posted` | `failed`

**PATCH `/reddit/posts/{id}`** — 编辑 title/body/subreddit（仅非 posted 状态）

### 8.2 评论

**POST `/reddit/comments/generate`**（手动 URL）

```json
{
  "account_id": 1,
  "target_post_url": "https://www.reddit.com/r/BabyBumps/comments/abc123/...",
  "include_site_url": true
}
```

**POST `/reddit/comments/generate-batch`**（关键词搜索）

```json
{
  "account_id": 1,
  "subreddit": "BabyBumps",
  "keyword": "baby monitor",
  "post_urls": ["https://..."],
  "include_site_url": true
}
```

`discover_source` 自动标记为 `keyword_search`。

**POST `/reddit/comments/{id}/approve|reject|publish`** — 同发帖。

### 8.3 帖子发现（预留全自动）

**GET `/reddit/discover/search`**

| 参数 | 说明 |
|------|------|
| subreddit | 必填 |
| keyword | 必填 |
| limit | 默认 10，最大 25 |
| auto_discover | 默认 false；true 时在响应 meta 中标记 `auto_mode: true` |
| auto_queue | 默认 false；true 且 auto_discover=true 时，自动为每条结果创建 `reddit_comments`（status=pending_review），**当前仅 API 预留，UI 不传此参数** |

响应：

```json
{
  "items": [
    {
      "title": "...",
      "url": "https://reddit.com/r/.../comments/...",
      "thing_id": "t3_abc123",
      "subreddit": "BabyBumps",
      "score": 42,
      "num_comments": 15,
      "created_utc": 1725000000
    }
  ],
  "meta": { "auto_mode": false, "queued_count": 0 }
}
```

---

## 9. 前端 UI

在 `/social` 页面新增 **「Reddit 运营」** Tab（`SocialScheduler.vue` 或拆分为 `RedditOperations.vue` 子组件）。

### 9.1 账号区

- 显示 Reddit 连接状态（`/reddit/status`）。
- 「连接 Reddit」按钮 → 打开 OAuth URL。
- 已连接账号列表（来自 `social_accounts` where platform=reddit）。

### 9.2 发帖区

- 表单：类型、子版块、关键词、是否带链接、账号。
- 「AI 生成」→ 弹出/内联编辑 title + body。
- 审核列表：pending_review / approved 条目，支持编辑、批准、发布。
- 历史：posted / failed 及错误信息。

### 9.3 评论区

- **子 Tab A — 粘贴 URL**：输入 URL → 生成 → 入审核列表。
- **子 Tab B — 关键词搜索**：子版块 + 关键词 → 搜索结果表格（勾选）→ 批量生成评论 → 审核列表。
- 审核列表统一：编辑 body、批准、发布、拒绝。

### 9.4 OAuth 回调处理

`/social` 页面 onMounted 检测 query `reddit=connected|error`，显示 ElMessage。

---

## 10. 错误处理

| 场景 | 行为 |
|------|------|
| Reddit 未配置 | OAuth/start 返回 503；publish 走 mock 或 503（可配置） |
| Token 过期 | 自动 refresh；失败则 status=failed，提示重新 OAuth |
| 403/429 | CircuitBreaker 打开，error_message 记录，支持手动重试 publish |
| 无效 subreddit | Reddit API 错误透传 |
| URL 解析失败 | 400，提示正确 Reddit 帖子 URL 格式 |

---

## 11. 安全与合规

- access_token / refresh_token 存 DB，API 响应不返回 token 明文。
- 所有写操作需 `require_site_write`。
- 数据按 `site_id` 隔离。
- 审核队列强制人工确认（当前阶段），降低 Reddit 封号风险。
- User-Agent 必须配置（Reddit API 要求）。

---

## 12. 测试策略

项目暂无 `tests/` 目录，本期新增：

- `backend/tests/test_reddit_url_parser.py` — URL → thing_id 解析
- `backend/tests/test_reddit_client_mock.py` — mock 模式发帖/评论
- `backend/scripts/test_reddit_oauth.py` — 配置检查 + oauth/start 冒烟（参照 test_gsc_oauth.py）

---

## 13. 文件清单

| 操作 | 路径 |
|------|------|
| 新建 | `backend/app/models/reddit.py` |
| 新建 | `backend/app/schemas/reddit.py` |
| 新建 | `backend/app/services/reddit_client.py` |
| 新建 | `backend/app/services/reddit_oauth.py` |
| 新建 | `backend/app/api/v1/reddit.py` |
| 新建 | `backend/alembic/versions/xxxx_reddit_operations.py` |
| 新建 | `backend/tests/test_reddit_url_parser.py` |
| 新建 | `backend/tests/test_reddit_client_mock.py` |
| 新建 | `backend/scripts/test_reddit_oauth.py` |
| 新建 | `frontend/src/api/reddit.ts` |
| 新建 | `frontend/src/components/RedditOperations.vue` |
| 修改 | `backend/app/core/config.py` |
| 修改 | `backend/app/models/__init__.py` |
| 修改 | `backend/app/api/v1/__init__.py` |
| 修改 | `backend/app/services/ai_writer.py` |
| 修改 | `backend/app/services/pulseforge_client.py`（RedditClient 委托 reddit_client） |
| 修改 | `backend/.env.example` |
| 修改 | `frontend/src/views/SocialScheduler.vue` |
| 修改 | `使用手册.md` |

---

## 14. 后续扩展（不在本期）

- Celery Beat 任务调用 `auto_discover=true&auto_queue=true` 全自动评论入队。
- 发帖定时发布（scheduled_at）。
- Reddit 帖子表现回流（karma、回复数）写入 engagement 字段。
- 多 Reddit 账号轮换策略。
