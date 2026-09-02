# Reddit 运营模块 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为 SEO Platform 增加 Reddit 子版块发帖与评论的审核队列模块，含 OAuth 连接、AI 内容生成、人工审核发布，并预留全自动发现接口。

**Architecture:** 独立 `/api/v1/reddit/*` 路由 + `reddit_posts` / `reddit_comments` 两张审核队列表；OAuth/token 复用 `social_accounts`；Reddit API 封装在 `reddit_client.py`（mock 回退）；前端在 `/social` 新增 Reddit 运营 Tab。

**Tech Stack:** FastAPI, SQLAlchemy, Alembic, httpx, Vue3 + Element Plus, DeepSeek AI, pytest

## Global Constraints

- 平台范围：仅 Reddit；发帖类型仅 `consultation` 与 `experience`
- 执行方式：审核队列（draft → pending_review → approved → posted）；当前 UI 不启用全自动
- 评论入口：手动 URL + 关键词搜索；`auto_discover` / `auto_queue` 仅 API 预留
- 站点 URL 默认从 `client_sites.domain` 读取（前缀 `https://`）
- OAuth Scope：`identity read submit`；duration=permanent
- 限流：Reddit 账号 10 秒 1 次（沿用 `rate_limiter` reddit 配置）
- User-Agent 必填：`REDDIT_USER_AGENT`
- 所有写操作使用 `require_site_write`；数据按 `site_id` 隔离
- Token 不得出现在 API 响应中

---

## File Map

| File | Responsibility |
|------|----------------|
| `backend/app/core/config.py` | Reddit OAuth 环境变量 |
| `backend/app/models/reddit.py` | `RedditPost`, `RedditComment` ORM |
| `backend/app/schemas/reddit.py` | Pydantic 请求/响应模型 |
| `backend/app/services/reddit_client.py` | Reddit API + mock + URL 解析 |
| `backend/app/services/reddit_oauth.py` | OAuth start/callback/token refresh |
| `backend/app/services/ai_writer.py` | Reddit 帖/评论 AI 生成 |
| `backend/app/api/v1/reddit.py` | 全部 Reddit REST 端点 |
| `backend/app/services/pulseforge_client.py` | `RedditClient` 委托真实 client |
| `frontend/src/api/reddit.ts` | 前端 API 封装 |
| `frontend/src/components/RedditOperations.vue` | Reddit 运营 UI |
| `frontend/src/views/SocialScheduler.vue` | 挂载 Reddit Tab |

---

### Task 1: 配置与 URL 解析工具

**Files:**
- Create: `backend/app/services/reddit_client.py`
- Modify: `backend/app/core/config.py`
- Modify: `backend/.env.example`
- Create: `backend/tests/test_reddit_url_parser.py`

**Interfaces:**
- Produces: `parse_reddit_post_url(url: str) -> tuple[str, str]` 返回 `(thing_id, subreddit)`；无效 URL 抛 `ValueError`
- Produces: `settings.REDDIT_CLIENT_ID`, `REDDIT_CLIENT_SECRET`, `REDDIT_REDIRECT_URI`, `REDDIT_USER_AGENT`
- Produces: `reddit_client.is_reddit_configured() -> bool`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_reddit_url_parser.py
import pytest
from app.services.reddit_client import parse_reddit_post_url


def test_parse_standard_reddit_url():
    url = "https://www.reddit.com/r/BabyBumps/comments/abc123/title_slug/"
    thing_id, subreddit = parse_reddit_post_url(url)
    assert thing_id == "t3_abc123"
    assert subreddit == "BabyBumps"


def test_parse_old_reddit_url():
    url = "https://old.reddit.com/r/parenting/comments/xyz789/some_post"
    thing_id, subreddit = parse_reddit_post_url(url)
    assert thing_id == "t3_xyz789"
    assert subreddit == "parenting"


def test_parse_invalid_url_raises():
    with pytest.raises(ValueError, match="Invalid Reddit post URL"):
        parse_reddit_post_url("https://google.com")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd c:\SEO\backend && conda run -n SEO python -m pytest tests/test_reddit_url_parser.py -v`  
Expected: FAIL with `ImportError` or `cannot import name 'parse_reddit_post_url'`

- [ ] **Step 3: Add config fields and implement parser**

`config.py` 追加：

```python
REDDIT_CLIENT_ID: Optional[str] = None
REDDIT_CLIENT_SECRET: Optional[str] = None
REDDIT_REDIRECT_URI: str = "http://127.0.0.1:8000/api/v1/reddit/oauth/callback"
REDDIT_USER_AGENT: str = "SEOPlatform/1.0"
```

`reddit_client.py` 实现：

```python
import re

_REDDIT_POST_RE = re.compile(
    r"reddit\.com/r/(?P<subreddit>[^/]+)/comments/(?P<post_id>[a-z0-9]+)",
    re.IGNORECASE,
)

def parse_reddit_post_url(url: str) -> tuple[str, str]:
    m = _REDDIT_POST_RE.search(url.strip())
    if not m:
        raise ValueError(f"Invalid Reddit post URL: {url}")
    return f"t3_{m.group('post_id')}", m.group("subreddit")

def is_reddit_configured() -> bool:
    from app.core.config import settings
    return bool(settings.REDDIT_CLIENT_ID and settings.REDDIT_CLIENT_SECRET)
```

`.env.example` 追加 Reddit 配置块（含注释说明 Reddit App 创建步骤）。

- [ ] **Step 4: Run test to verify it passes**

Run: `cd c:\SEO\backend && conda run -n SEO python -m pytest tests/test_reddit_url_parser.py -v`  
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add backend/app/core/config.py backend/app/services/reddit_client.py backend/.env.example backend/tests/test_reddit_url_parser.py
git commit -m "feat(reddit): add config and URL parser utilities"
```

---

### Task 2: 数据库迁移与 ORM 模型

**Files:**
- Create: `backend/app/models/reddit.py`
- Create: `backend/alembic/versions/g1h2i3j4k5l6_reddit_operations.py`
- Modify: `backend/app/models/__init__.py`

**Interfaces:**
- Produces: `RedditPost` ORM with fields per design spec §4.1
- Produces: `RedditComment` ORM with fields per design spec §4.2
- Consumes: FK to `client_sites.id`, `social_accounts.id`

- [ ] **Step 1: Create ORM models**

```python
# backend/app/models/reddit.py
class RedditPost(Base):
    __tablename__ = "reddit_posts"
    # id, site_id, account_id, post_type, subreddit, keyword,
    # title, body, site_url, status, reddit_post_id, reddit_permalink,
    # error_message, created_at, updated_at
    account: Mapped["SocialAccount"] = relationship()
    # default status="draft"

class RedditComment(Base):
    __tablename__ = "reddit_comments"
    # id, site_id, account_id, target_post_url, target_thing_id, subreddit,
    # target_post_title, body, keyword, discover_source, status,
    # reddit_comment_id, error_message, created_at, updated_at
```

- [ ] **Step 2: Write Alembic migration**

```bash
cd c:\SEO\backend
conda run -n SEO python -m alembic revision -m "reddit_operations"
```

编辑生成的 migration：`create_table reddit_posts` 和 `reddit_comments`，含 index on `(site_id, status)`。

- [ ] **Step 3: Register models and run migration**

`models/__init__.py` import `RedditPost`, `RedditComment`。

Run: `conda run -n SEO python -m alembic upgrade head`  
Expected: migration succeeds, tables exist

- [ ] **Step 4: Commit**

```bash
git add backend/app/models/reddit.py backend/app/models/__init__.py backend/alembic/versions/g1h2i3j4k5l6_reddit_operations.py
git commit -m "feat(reddit): add reddit_posts and reddit_comments tables"
```

---

### Task 3: Pydantic Schemas

**Files:**
- Create: `backend/app/schemas/reddit.py`

**Interfaces:**
- Produces: `RedditPostGenerateIn`, `RedditPostUpdate`, `RedditPostOut`
- Produces: `RedditCommentGenerateIn`, `RedditCommentBatchGenerateIn`, `RedditCommentUpdate`, `RedditCommentOut`
- Produces: `RedditDiscoverItem`, `RedditDiscoverResponse`, `RedditStatusOut`, `RedditOAuthStartOut`
- Produces: `PostType = Literal["consultation", "experience"]`
- Produces: `ReviewStatus = Literal["draft","pending_review","approved","rejected","posting","posted","failed"]`

- [ ] **Step 1: Implement all schemas per design §8**

包含 `RedditCommentBatchGenerateIn` 字段：`account_id`, `subreddit`, `keyword`, `post_urls: list[str]`, `include_site_url: bool = False`

- [ ] **Step 2: Commit**

```bash
git add backend/app/schemas/reddit.py
git commit -m "feat(reddit): add pydantic schemas"
```

---

### Task 4: Reddit API Client（mock + 真实）

**Files:**
- Modify: `backend/app/services/reddit_client.py`
- Create: `backend/tests/test_reddit_client_mock.py`

**Interfaces:**
- Produces: `class RedditApiClient:` with methods:
  - `__init__(self, access_token: str | None, refresh_token: str | None = None)`
  - `submit_post(self, subreddit: str, title: str, body: str) -> dict` 返回 `{"id": "t3_...", "permalink": "..."}`
  - `submit_comment(self, thing_id: str, body: str) -> dict` 返回 `{"id": "t1_..."}`
  - `get_post_context(self, thing_id: str) -> dict` 返回 `{"title": str, "body": str, "subreddit": str}`
  - `search_posts(self, subreddit: str, keyword: str, limit: int = 10) -> list[dict]`
  - `get_me(self) -> dict` 返回 `{"name": "username"}`
  - `refresh_access_token(self) -> dict` 返回新 token 字段
- Produces: `get_reddit_client_for_account(account: SocialAccount) -> RedditApiClient`

- [ ] **Step 1: Write failing mock test**

```python
# backend/tests/test_reddit_client_mock.py
from app.services.reddit_client import RedditApiClient

def test_mock_submit_post():
    client = RedditApiClient(access_token=None)  # unconfigured => mock
    result = client.submit_post("BabyBumps", "Test title", "Test body")
    assert result["id"].startswith("t3_")
    assert "permalink" in result

def test_mock_submit_comment():
    client = RedditApiClient(access_token=None)
    result = client.submit_comment("t3_abc123", "Nice post!")
    assert result["id"].startswith("t1_")
```

- [ ] **Step 2: Run test — expect FAIL**

Run: `conda run -n SEO python -m pytest tests/test_reddit_client_mock.py -v`

- [ ] **Step 3: Implement RedditApiClient**

- Mock 模式：`not is_reddit_configured() or not access_token`
- 真实模式：httpx + `Authorization: bearer {token}`, `User-Agent: settings.REDDIT_USER_AGENT`
- `submit_post`: POST `https://oauth.reddit.com/api/submit` form data `kind=self, sr, title, text`
- `submit_comment`: POST `https://oauth.reddit.com/api/comment` form data `thing, text`
- `search_posts`: GET `https://oauth.reddit.com/r/{subreddit}/search?q={keyword}&restrict_sr=1&limit={limit}`
- 集成 `acquire_token("reddit", account_id)` 和 `CircuitBreaker`（account_id 通过 factory 传入）

- [ ] **Step 4: Run tests — expect PASS**

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/reddit_client.py backend/tests/test_reddit_client_mock.py
git commit -m "feat(reddit): implement Reddit API client with mock fallback"
```

---

### Task 5: Reddit OAuth 服务

**Files:**
- Create: `backend/app/services/reddit_oauth.py`
- Create: `backend/scripts/test_reddit_oauth.py`

**Interfaces:**
- Produces: `is_reddit_oauth_configured() -> bool`
- Produces: `prepare_oauth_session(db, user_id, site_id) -> str` 返回 auth URL
- Produces: `consume_oauth_session(db, state) -> tuple[int, int]` 返回 `(user_id, site_id)`
- Produces: `exchange_code_for_tokens(code: str) -> dict`
- Produces: `upsert_reddit_account(db, user_id, site_id, tokens, username) -> SocialAccount`
- Produces: `frontend_redirect(params: dict) -> str`

OAuth state 存 `social_accounts.config` 临时字段或新建内存/redis — **采用 `social_accounts.config["_oauth_state"]` 存 nonce**（简单方案），或参照 GSC 单独字段。推荐：在 `social_accounts` 无记录时创建 pending 记录存 state 到 `config`。

- [ ] **Step 1: Implement reddit_oauth.py following gsc_client pattern**

授权 URL：
```
https://www.reddit.com/api/v1/authorize?client_id=...&response_type=code&state=...&redirect_uri=...&duration=permanent&scope=identity read submit
```

Token 交换：POST `https://www.reddit.com/api/v1/access_token`，Basic Auth `(client_id, client_secret)`

- [ ] **Step 2: Write smoke script test_reddit_oauth.py**

参照 `scripts/test_gsc_oauth.py`：检查 env 配置 + 带 JWT 调 `/reddit/oauth/start`

- [ ] **Step 3: Commit**

```bash
git add backend/app/services/reddit_oauth.py backend/scripts/test_reddit_oauth.py
git commit -m "feat(reddit): add OAuth service and smoke script"
```

---

### Task 6: AI 生成方法

**Files:**
- Modify: `backend/app/services/ai_writer.py`

**Interfaces:**
- Produces: `DeepSeekClient.generate_reddit_post(post_type: str, subreddit: str, keyword: str, site_url: str | None) -> dict` 返回 `{"title": str, "body": str}`
- Produces: `DeepSeekClient.generate_reddit_comment(post_title: str, post_body: str, subreddit: str, site_url: str | None) -> str`

- [ ] **Step 1: Add prompt templates**

`REDDIT_CONSULTATION_PROMPT` — 英文咨询帖，无链接  
`REDDIT_EXPERIENCE_PROMPT` — 英文经验帖，可选 site_url  
`REDDIT_COMMENT_PROMPT` — 英文评论，可选 site_url

- [ ] **Step 2: Implement methods returning parsed JSON/text**

Run manual smoke（若 DEEPSEEK_API_KEY 已配置）：

```bash
conda run -n SEO python -c "from app.services.ai_writer import get_ai_client; c=get_ai_client(); print(c.generate_reddit_post('consultation','BabyBumps','baby monitor',None))"
```

- [ ] **Step 3: Commit**

```bash
git add backend/app/services/ai_writer.py
git commit -m "feat(reddit): add AI generation for posts and comments"
```

---

### Task 7: Reddit API 路由 — OAuth + Status

**Files:**
- Create: `backend/app/api/v1/reddit.py`
- Modify: `backend/app/api/v1/__init__.py`

**Interfaces:**
- Produces endpoints:
  - `GET /reddit/status` → `RedditStatusOut`
  - `GET /reddit/oauth/start` → `RedditOAuthStartOut`
  - `GET /reddit/oauth/callback` → RedirectResponse

- [ ] **Step 1: Implement status endpoint**

返回 `configured`, `connected`, `accounts: list[{id, account_name}]`

- [ ] **Step 2: Implement oauth/start and oauth/callback**

callback 完成后 `upsert_reddit_account`，redirect 到 `{FRONTEND_URL}/social?reddit=connected`

- [ ] **Step 3: Register router**

```python
# __init__.py
from app.api.v1.reddit import router as reddit_router
api.include_router(reddit_router, prefix="/reddit", tags=["Reddit 运营"])
```

- [ ] **Step 4: Smoke test**

Run backend + `python scripts/test_reddit_oauth.py`

- [ ] **Step 5: Commit**

```bash
git add backend/app/api/v1/reddit.py backend/app/api/v1/__init__.py
git commit -m "feat(reddit): add OAuth and status API endpoints"
```

---

### Task 8: Reddit API 路由 — 发帖审核流

**Files:**
- Modify: `backend/app/api/v1/reddit.py`

**Interfaces:**
- Produces:
  - `POST /reddit/posts/generate`
  - `GET /reddit/posts?status=`
  - `PATCH /reddit/posts/{id}`
  - `POST /reddit/posts/{id}/approve`
  - `POST /reddit/posts/{id}/reject`
  - `POST /reddit/posts/{id}/publish`

- [ ] **Step 1: Implement generate**

1. 验证 account 归属 site
2. 从 `ctx.site.domain` 构建 site_url（experience + include_site_url 时）
3. 调 AI 生成 title/body
4. 创建 `RedditPost` status=`pending_review`

- [ ] **Step 2: Implement list + patch**

PATCH 仅允许 status in (`draft`, `pending_review`, `approved`)

- [ ] **Step 3: Implement approve / reject**

approve → status=`approved`; reject → status=`rejected`

- [ ] **Step 4: Implement publish**

1. 验证 status=`approved`
2. status=`posting`
3. `get_reddit_client_for_account(account).submit_post(...)`
4. 成功 → `posted` + 存 reddit_post_id/permalink；失败 → `failed` + error_message

- [ ] **Step 5: Manual API test via Swagger**

`http://127.0.0.1:8000/docs` — generate → approve → publish（mock 模式）

- [ ] **Step 6: Commit**

```bash
git add backend/app/api/v1/reddit.py
git commit -m "feat(reddit): add post review queue API"
```

---

### Task 9: Reddit API 路由 — 评论与发现

**Files:**
- Modify: `backend/app/api/v1/reddit.py`

**Interfaces:**
- Produces:
  - `POST /reddit/comments/generate`
  - `POST /reddit/comments/generate-batch`
  - `GET /reddit/comments?status=`
  - `PATCH /reddit/comments/{id}`
  - `POST /reddit/comments/{id}/approve|reject|publish`
  - `GET /reddit/discover/search`

- [ ] **Step 1: Implement comments/generate (manual URL)**

1. `parse_reddit_post_url(url)` → thing_id, subreddit
2. `get_post_context(thing_id)` 读帖（mock 返回假标题/正文）
3. AI 生成评论 body
4. 创建 `RedditComment` discover_source=`manual_url`, status=`pending_review`

- [ ] **Step 2: Implement comments/generate-batch**

对每个 `post_urls` 条目重复上述流程，discover_source=`keyword_search`

- [ ] **Step 3: Implement discover/search**

```python
@router.get("/discover/search")
def discover_search(
    subreddit: str,
    keyword: str,
    limit: int = Query(10, le=25),
    auto_discover: bool = False,
    auto_queue: bool = False,
    account_id: int | None = None,
    ...
):
```

- `auto_queue=True` 且 `auto_discover=True` 且 `account_id` 有值时：为每条搜索结果创建 `RedditComment`（status=`pending_review`, discover_source=`auto_discover`）
- 响应 `meta: {"auto_mode": auto_discover, "queued_count": N}`

- [ ] **Step 4: Implement comment approve/reject/publish**

publish 调 `submit_comment(target_thing_id, body)`

- [ ] **Step 5: Commit**

```bash
git add backend/app/api/v1/reddit.py
git commit -m "feat(reddit): add comment queue and discover search API"
```

---

### Task 10: 更新 pulseforge RedditClient 委托

**Files:**
- Modify: `backend/app/services/pulseforge_client.py`

**Interfaces:**
- Modifies: `RedditClient.publish_post` 在有 token 时委托 `RedditApiClient.submit_post`

- [ ] **Step 1: Update RedditClient.publish_post to use reddit_client when configured**

保持现有 rate limiter / circuit breaker 包装；无 token 时仍 mock。

- [ ] **Step 2: Commit**

```bash
git add backend/app/services/pulseforge_client.py
git commit -m "refactor(reddit): delegate RedditClient to RedditApiClient"
```

---

### Task 11: 前端 API 封装

**Files:**
- Create: `frontend/src/api/reddit.ts`

**Interfaces:**
- Produces typed functions matching all `/reddit/*` endpoints
- Types: `RedditPost`, `RedditComment`, `RedditDiscoverItem`, `PostType`, `ReviewStatus`

- [ ] **Step 1: Implement reddit.ts**

```typescript
export function getRedditStatus() { return http.get('/reddit/status') }
export function startRedditOAuth() { return http.get('/reddit/oauth/start') }
export function generateRedditPost(payload: RedditPostGenerateIn) { ... }
export function listRedditPosts(params?: { status?: string }) { ... }
export function approveRedditPost(id: number) { ... }
export function publishRedditPost(id: number) { ... }
// ... comments + discover
```

- [ ] **Step 2: Commit**

```bash
git add frontend/src/api/reddit.ts
git commit -m "feat(reddit): add frontend API client"
```

---

### Task 12: 前端 Reddit 运营 UI

**Files:**
- Create: `frontend/src/components/RedditOperations.vue`
- Modify: `frontend/src/views/SocialScheduler.vue`

**Interfaces:**
- Consumes: all functions from `@/api/reddit`

- [ ] **Step 1: Create RedditOperations.vue with three sections**

1. **账号区**：连接状态 + OAuth 按钮 + 账号下拉
2. **发帖区**：类型/子版块/关键词表单 → AI 生成 → 编辑 → 审核列表（approve/publish）
3. **评论区**：子 Tab「粘贴 URL」和「关键词搜索」→ 审核列表

- [ ] **Step 2: Add tab to SocialScheduler.vue**

```vue
<el-tab-pane label="Reddit 运营" name="reddit">
  <RedditOperations />
</el-tab-pane>
```

- [ ] **Step 3: Handle OAuth callback query params**

onMounted: if `route.query.reddit === 'connected'` → ElMessage.success

- [ ] **Step 4: Manual UI test**

启动 `npm run dev`，访问 `/social` → Reddit 运营 Tab，测试生成/审核流程（mock 后端）

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/RedditOperations.vue frontend/src/views/SocialScheduler.vue
git commit -m "feat(reddit): add Reddit operations UI tab"
```

---

### Task 13: 文档更新

**Files:**
- Modify: `使用手册.md`

- [ ] **Step 1: Add section 5.x Reddit 运营**

包含：Reddit App 创建步骤、OAuth 连接、发帖/评论审核流程、API 列表、`auto_discover` 预留说明

- [ ] **Step 2: Update 目录 and API 参考**

- [ ] **Step 3: Commit**

```bash
git add 使用手册.md docs/superpowers/specs/2026-09-02-reddit-operations-design.md docs/superpowers/plans/2026-09-02-reddit-operations.md
git commit -m "docs: add Reddit operations design, plan, and user manual"
```

---

## Spec Self-Review

| Spec 要求 | 对应 Task |
|-----------|-----------|
| Reddit OAuth | Task 1, 5, 7 |
| 咨询帖 + 经验分享帖 | Task 6, 8 |
| 审核队列 | Task 8, 9 |
| 手动 URL 评论 | Task 9 |
| 关键词搜索 | Task 9, 12 |
| auto_discover / auto_queue 预留 | Task 9 |
| site_url 从 client_sites | Task 8 |
| 限流/熔断 | Task 4 |
| Mock 开发模式 | Task 4 |
| 前端 Reddit Tab | Task 11, 12 |
| 测试 | Task 1, 4, 5 |
| 不在本期范围 | 未纳入 Task |

无 TBD/placeholder。类型名跨 Task 一致。

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-09-02-reddit-operations.md`.

**Two execution options:**

1. **Subagent-Driven (recommended)** — 每个 Task 派发独立 subagent，Task 之间做 review，迭代快
2. **Inline Execution** — 在本会话按 Task 顺序直接实现，每 2–3 个 Task 设检查点

**Which approach?**
