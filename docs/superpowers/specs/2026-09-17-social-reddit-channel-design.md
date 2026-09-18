# 社交分发重构：Reddit 为首个渠道

> **日期：** 2026-09-17  
> **状态：** 待用户审阅  
> **范围：** 将 `/social` 收成 Reddit 创作 / 审核 / 发布工作台；预留渠道接口，本期不接入其它平台  
> **前置：** [2026-09-02 Reddit 运营设计](./2026-09-02-reddit-operations-design.md) 的发帖/评论/OAuth 行为仍然有效；本文覆盖「社交模块怎么改」以及裁剪后如何接回。

---

## 1. 背景与目标

当前产品只保留综合大屏、社交分发、排名监控。社交分发是 PulseForge / LinkedIn / Twitter / Facebook / Reddit 的通用账号 + 发帖调度。旧 Reddit 运营（OAuth、AI 生成咨询/经验帖、评论搜索、强制审核队列）已从路由和页面拿掉；`r002_trim_modules` 删除了 `reddit_posts` / `reddit_comments`。模型、schema、`reddit_client` / `reddit_oauth`、`RedditOperations.vue`、`frontend/src/api/reddit.ts`、`ai_writer` 的 Reddit 生成方法仍在仓库里。

本期目标：

1. **恢复** Reddit 创作与发布：发帖（咨询 / 经验）+ 评论（粘贴 URL / 关键词搜索），强制人工审核后再发。
2. **重构** `/social`：页面只做 Reddit；通用 PulseForge 等发帖表单从 UI 移除。
3. **预留** `SocialChannel` 协议，以后加平台时接同一套「连接账号 + 发布」，本期只实现 `RedditChannel`。

不在本期：其它社交平台 UI、Celery 全自动评论、发帖定时、karma 回流、多账号轮换。

---

## 2. 产品形态

| 项 | 决策 |
|----|------|
| 侧栏 | 仍叫「社交分发」，路由 `/social` |
| 页面 | 整页 Reddit 工作台，无通用账号/发帖 Tab |
| 账号 | OAuth「连接 Reddit」；选择已连接的 `u/xxx` |
| 发帖 | 咨询帖 / 经验分享 → AI 生成入队 → 编辑 / 批准 → 发布 |
| 评论 | 粘贴帖 URL，或子版块+关键词搜索勾选 → 生成 → 审核 → 发布 |
| 审核 | 必须：`publish` 仅接受 `approved` |
| 其它平台 | UI 不出现；`/api/v1/social/posts` 保留但不挂页面 |

OAuth 回调：`/social?reddit=connected` 或 `?reddit=error&message=`，页面弹出成功/失败提示。

---

## 3. 架构

```
/social  SocialScheduler.vue（壳）
    └── RedditOperations.vue（账号 + 发帖审核 + 评论审核）

GET/POST /api/v1/reddit/*
    OAuth / 状态 / 发帖队列 / 评论队列 / 搜索

social_accounts          Token 仓库（platform=reddit）
reddit_posts             发帖审核队列
reddit_comments          评论审核队列

SocialChannel (Protocol)
    └── RedditChannel    本期唯一实现
```

职责：

| 单元 | 做什么 | 依赖 |
|------|--------|------|
| `SocialChannel` | 定义 connect / publish_post / publish_comment / search | 无具体平台 |
| `RedditChannel` | 包装现有 `reddit_oauth` + `reddit_client` | Reddit API 或 Mock |
| `api/v1/reddit.py` | HTTP：生成、审核、发布、发现 | Channel、AI、DB |
| `ai_writer` | 生成英文帖/评论 JSON 或正文 | DeepSeek |
| `social_accounts` | 存 OAuth token，响应不回传明文 token | — |

以后加 LinkedIn：新 `LinkedInChannel` + 可选新表或复用 `social_posts`，在 `/social` 加渠道切换。本期不写空实现类。

---

## 4. SocialChannel 协议

文件：`backend/app/services/social_channel.py`

```python
class SocialChannel(Protocol):
    platform_name: str  # 例如 "reddit"

    def is_configured(self) -> bool: ...
    def get_authorization_url(self, user_id: int, site_id: int) -> str: ...
    def publish_post(self, account: SocialAccount, title: str, body: str, **kwargs) -> PublishResult: ...
    def publish_comment(self, account: SocialAccount, thing_id: str, body: str) -> PublishResult: ...
    def search(self, account: SocialAccount, *, subreddit: str, keyword: str, limit: int) -> list[SearchItem]: ...
```

`PublishResult`：`success`、`platform_id`、`permalink`、`error`。  
`kwargs` 对 Reddit 含 `subreddit`。未配置客户端或无 token 时，Reddit 实现走 Mock（与现 `RedditApiClient` 一致），不在 OAuth start 上 Mock：未配置时 `oauth/start` 返回 503。

`get_platform_client`（PulseForge 适配层）保持不动；通用 `social/posts/send-now` 仍走它。Reddit 审核发布 **不** 走 `social_posts`。

---

## 5. 数据

新迁移 `r003_restore_reddit_queues`，`down_revision = r002_trim_modules`，按 `g1h2i3j4k5l6` 原表结构重建 `reddit_posts`、`reddit_comments`（含 site/account/status 索引与外键）。

状态机（发帖与评论相同）：

```
draft → pending_review → approved → posting → posted
                                      ↘ failed
pending_review / approved / draft → rejected
```

`POST .../generate` 直接写入 `pending_review`。  
`POST .../publish`：若 status ≠ `approved` → 400。发布中写 `posting`，成功 `posted` + Reddit id/permalink，失败 `failed` + `error_message`。`failed` 可在仍为 approved 语义下允许再次 publish（实现：`failed` 且曾批准过的记录允许重试，或 `failed` → 用户再点发布视为重试）。**明确：`failed` 允许再次调用 publish（重试），不必重新 approve。** `posted` 不可编辑、不可再发。

`site_service.delete_client_site`：先删该站 `reddit_posts` / `reddit_comments`，再删社交账号与关键词。

`models/__init__.py` 重新 export `RedditPost`、`RedditComment`。

---

## 6. API

挂载：`api.include_router(reddit_router, prefix="/reddit", tags=["Reddit 运营"])`。  
前缀完整路径：`/api/v1/reddit/...`。写操作走 `require_site_write`，按 `site_id` 隔离。

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/status` | `configured` / `connected` / reddit 账号列表 |
| GET | `/oauth/start` | `{ auth_url }`；未配置 503 |
| GET | `/oauth/callback` | 换 token，upsert `social_accounts`，重定向前端 |
| POST | `/posts/generate` | AI 生成，入 `pending_review` |
| GET | `/posts` | `?status=` |
| PATCH | `/posts/{id}` | title / body / subreddit；`posted` 不可改 |
| POST | `/posts/{id}/approve` | → `approved`（仅 `pending_review`） |
| POST | `/posts/{id}/reject` | → `rejected`（未发布） |
| POST | `/posts/{id}/publish` | Reddit 发帖；仅 `approved` 或 `failed` |
| POST | `/comments/generate` | 解析 URL，读帖上下文，AI 生成 |
| POST | `/comments/generate-batch` | `discover_source=keyword_search` |
| GET | `/comments` | `?status=` |
| PATCH | `/comments/{id}` | body |
| POST | `/comments/{id}/approve\|reject\|publish` | 同发帖 |
| GET | `/discover/search` | subreddit + keyword；`auto_discover` / `auto_queue` 仅 API 预留，UI 不传 |

生成入参与 2026-09-02 规格第 8 节一致。`site_url` 来自当前默认站点 `domain`（可被 `include_site_url=false` 关掉）。咨询帖生成时忽略链接。

通用社交 API（`/social/accounts`、`/social/posts`）**保留**。前端不再调用 posts；账号创建以 Reddit OAuth 为准，不提供手动填 Reddit token 的表单。

---

## 7. 前端

- `SocialScheduler.vue`：去掉「社交账号 / 发帖任务」双 Tab 和平台下拉；渲染 `RedditOperations.vue`。
- `DefaultLayout.vue`：副标题改为「Reddit 创作、审核与发布」；菜单名不变。
- `RedditOperations.vue`：接回现有交互（连接、发帖表单、评论 URL/搜索、审核表、编辑弹窗）。补齐拒绝按钮若当前模板缺失。
- `frontend/src/api/reddit.ts`：已与上表对齐，原则上不改路径。
- `frontend/src/api/social.ts`：可保留类型，页面不再使用发帖 CRUD。

---

## 8. AI 与外部依赖

- 使用已有 `AiWriter.generate_reddit_post` / `generate_reddit_comment`。
- 咨询帖：英文、第一人称、不硬广、不含链接，约 100–250 词。
- 经验帖：可自然提及产品；`include_site_url` 时文末软链。
- 评论：针对目标帖标题+正文，50–120 词。
- `DEEPSEEK_API_KEY` 未配置时 generate 返回明确错误（现有 `DeepSeekError`），不写假文案进队列。
- Reddit：未配 `REDDIT_CLIENT_ID` 时发布走 Mock；OAuth start 不可用。
- 限流：现有 reddit `rate_limiter`（约 10 秒 1 次）+ CircuitBreaker。发布前 access_token 过期则 refresh。

---

## 9. 错误处理

| 场景 | 行为 |
|------|------|
| 未配置 Reddit OAuth | `oauth/start` 503；页面显示 Mock 标签，发布仍可走 Mock |
| Token 刷新失败 | status=`failed`，提示重新连接 |
| 403 / 429 | 熔断，error_message 记录，允许对 `failed` 重试 publish |
| 无效帖 URL | 400，提示 Reddit 帖链接格式 |
| 无效 subreddit | Reddit 错误写入 error_message |
| 对非 approved/failed 调用 publish | 400 |
| AI 失败 | generate 接口 502/500，不入库 |

API 响应中的账号对象不含 `access_token`。

---

## 10. 测试与验收

恢复 / 补齐：

- `backend/tests/test_reddit_url_parser.py`
- `backend/tests/test_reddit_client_mock.py`
- 审核发布：生成（可 mock AI）→ approve → publish（Mock）状态为 `posted`

手动验收：

1. 登录 → `/social` 只有 Reddit 工作台，无 PulseForge 表单。
2. Mock 模式下生成咨询帖 → 编辑 → 批准 → 发布成功。
3. 粘贴合法 Reddit URL 生成评论 → 审核 → 发布。
4. 搜索选帖批量生成评论入队。
5. 未批准时点发布失败（400）。
6. `alembic upgrade head` 后存在 `reddit_posts` / `reddit_comments`。

浏览器：走通发帖与评论主路径（Mock）。

---

## 11. 文件清单

| 操作 | 路径 |
|------|------|
| 新建 | `backend/app/api/v1/reddit.py`（从 git 历史恢复并接到 Channel） |
| 新建 | `backend/app/services/social_channel.py` |
| 新建 | `backend/alembic/versions/r003_restore_reddit_queues.py` |
| 恢复 | `backend/tests/test_reddit_url_parser.py`、`test_reddit_client_mock.py` |
| 修改 | `backend/app/api/v1/__init__.py` |
| 修改 | `backend/app/models/__init__.py` |
| 修改 | `backend/app/services/site_service.py` |
| 修改 | `frontend/src/views/SocialScheduler.vue` |
| 修改 | `frontend/src/layout/DefaultLayout.vue` |
| 修改 | `frontend/src/components/RedditOperations.vue`（拒绝/缺口） |
| 修改 | `使用手册.md` |

已存在、以接线为主：`models/reddit.py`、`schemas/reddit.py`、`services/reddit_client.py`、`reddit_oauth.py`、`ai_writer.py`、`frontend/src/api/reddit.ts`。

---

## 12. 明确不做

- 不删 `social_posts` 表，不改 PulseForge 真实发帖逻辑。
- 不实现 LinkedIn/Twitter/Facebook 渠道类。
- 不做 `auto_discover` UI，不接 Celery 自动入队。
- 不把 Reddit 帖/评论写入 `social_posts`。
- 不提交本规格以外的手册大改（只补 Reddit / 社交分发相关章节）。
