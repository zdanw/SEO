# Reddit 自动化 Campaign 功能实施计划

## Context

用户需要新增自动化功能：在指定时间段内自动执行 Reddit 发帖 / 评论 / 点赞等系列操作。

现状：发帖/评论已有完整定时发布链路（`scheduled_at` + beat 每 15 分钟扫描入队），点赞仅有同步 API 客户端 `vote()` 但无定时执行与记录，账号矩阵已有每日限额与风控（`RedditAccountProfile.risk_status`）。缺少的是「按时间段统筹多种动作、自动生成内容、自动发现互动目标」的 Campaign 编排层。

已确认需求：
1. 内容审核模式可配置：`auto`（AI 生成后直接定时发布）/ `review`（进入现有待审队列）
2. 点赞目标两类：相关讨论帖（养号）+ 自己账号刚发的帖子（提热度，voter ≠ author）
3. 需要前端管理界面

## 复用要点（已核实）

- 发布链路零改造：auto 模式生成内容置 `status="approved"` + `scheduled_at=随机时间`，现有 `publish_scheduled_reddit_content`（app/tasks/reddit_tasks.py:179）自动消费
- 评论生成：API 层 `_generate_comment_for_url`（app/api/v1/reddit.py:850），任务内已有 SimpleNamespace 伪装 ctx 的调用先例（reddit_tasks.py:363 `discover_reddit_discussions`）；社区风格 few-shot 复用 `fetch_community_examples`（app/services/reddit_style.py）
- 点赞：`RedditApiClient.vote(thing_id, direction)`（app/services/reddit_client.py:126）
- 风控/限额：`get_account_daily_usage`、`risk_status`（app/services/reddit_risk.py）
- 去重锁模式：Redis `set(nx, ex)` + 固定 task_id（`try_enqueue_publish`，reddit_tasks.py:40）
- 权限：`get_site_context`（读）/ `require_site_write`（写）
- AI 生成：DeepSeek API（硬性要求，禁用 OpenAI）

## 1. 数据模型（app/models/reddit.py 追加）

**RedditCampaign（reddit_campaigns）**
- id、site_id(FK, idx)、name(120)
- status: draft/running/paused/ended (idx)
- start_date/end_date (Date)、active_hour_start/end (Integer 0-23, UTC，与 best_hour_utc 约定一致)
- content_mode: auto/review
- daily_posts / daily_comments / daily_upvotes (Integer)
- self_upvote_enabled (Boolean)、self_upvote_delay_minutes (Integer, 默认 240)
- brand_id / product_id (FK nullable，生成上下文)
- account_ids (JSON 账号池)、last_tick_at、created_at/updated_at

**RedditCampaignAction（reddit_campaign_actions，执行记录）**
- id、campaign_id(FK CASCADE, idx)、site_id(idx)、account_id(idx)
- action_type: post/comment/upvote_related/upvote_self (idx)
- status: pending/executing/done/failed/skipped (idx)
- planned_at (DateTime tz, idx，随机化执行时间)、executed_at
- post_id / comment_id (FK nullable, idx)、target_thing_id(String20)、target_post_url
- error_message (Text)、skip_reason (String200)
- dedup_key (String150)
- **约束：`UniqueConstraint(campaign_id, dedup_key)`** 防重复计划
- dedup_key 取值：`upvote:{account_id}:{thing_id}`、`upvote_self:{voter_id}:{post_id}`、`post:{campaign}:{account}:{yyyymmdd}`

**RedditVoteLog（reddit_vote_logs）**
- id、site_id(idx)、account_id(idx)、thing_id(String20)、direction(1/0/-1)
- source: campaign/manual、campaign_action_id(FK nullable)、error_message、created_at
- **`unique(account_id, thing_id)` + upsert**（on_conflict_do_update 覆盖 direction）— 满足项目"唯一约束+upsert"硬约定，兼作"是否点过赞"查询依据

**RedditPost / RedditComment 加 campaign_id**：nullable FK `ondelete="SET NULL"` + index。手动内容不受影响，删 campaign 不删内容。

**迁移**：新建 `backend/alembic/versions/r016_reddit_campaign.py`（当前 head 为 r015_zernio_key_site_id，实施前用 `alembic heads` 复核）；`app/models/__init__.py` 补 import。

## 2. 调度执行引擎

新建 `backend/app/tasks/reddit_campaign_tasks.py`（reddit_ 前缀自动命中 social 队列路由），核心逻辑在 `backend/app/services/reddit_campaign.py`。

**beat**：`campaign_tick` 每 15 分钟与发布任务错峰（`crontab(minute="7,22,37,52")`），入口 Redis 单飞锁 `lock:reddit:campaign_tick`（nx, ex=600）。celery_app.py 的 include 加模块。

**tick 流程**：
1. 扫 `status=="running"` 且 start_date<=今天<=end_date 的 campaign
2. 账号过滤：`is_active` + `risk_status=="normal"` + 未超账号每日限额 + campaign 当日配额未满。**配额查库统计**：count 当日 action（type 匹配、status != "skipped"、planned_at 在今日），比 Redis 抗重启
3. 随机化：`planned_at = now + random(0~12min)` 裁剪到活跃时段；账号 random.shuffle；同账号相邻动作强制间隔 >=25 分钟（规避机器人特征）
4. 生成动作：
   - post/comment：复用 `_generate_comment_for_url`（callback 内设 campaign_id、discover_source="campaign"）；帖子生成核心抽为 `generate_post_for_campaign()` 到 `app/services/reddit_content.py`（复用 reddit_style few-shot + DeepSeek）。**auto**：status="approved"+scheduled_at=planned_at 交现有发布链；**review**：status="pending_review" 进待审队列，action 由对账步骤闭环
   - upvote_related：product keywords/persona 社区 `client.search_posts` 取候选，过滤 7 天内、score 2-50、非池内账号所发、该账号未赞过（查 RedditVoteLog）；无候选 → skipped(no_candidate)
   - upvote_self：池内账号近 self_upvote_delay_minutes 发布的 posted 帖，**投票人必须是池内其他账号**（voter != author），排除已赞对
5. 对账：post/comment action 关联内容 posted→done；failed→failed（带 error）；被删→skipped
6. 执行到期点赞：`pending + planned_at<=now + type in (upvote_*)` → 锁键 `enqueue:reddit:campaign_action:{id}` 入队 `execute_campaign_action(action_id)`（soft=55/hard=70 与现有常量一致）。执行时原子 claim（`UPDATE ... WHERE status=='pending'`，仿 `_claim_post`）→ `client.vote` → RedditVoteLog upsert → done；RedditApiError → failed；**不自动重试**，留痕供 UI 重试；回收 executing 超 15 分钟僵尸 → failed

## 3. API（app/api/v1/reddit.py 追加）

- `GET /reddit/automation/campaigns` — 列表 + 今日执行统计
- `POST /reddit/automation/campaigns`（201；校验日期区间、0-23 小时、配额>=0、账号属于本站）
- `GET/PATCH/DELETE /reddit/automation/campaigns/{id}`（DELETE 204 级联删 action）
- `POST /reddit/automation/campaigns/{id}/start`、`/pause`
- `GET /reddit/automation/campaigns/{id}/actions?status=&action_type=`
- `POST /reddit/automation/actions/{action_id}/retry`（failed→pending）

Schemas（app/schemas/reddit.py）：`RedditCampaignCreate/Update/Out`、`RedditCampaignActionOut`，风格对齐现有 `ConfigDict(from_attributes=True)`。

## 4. 前端（Vue3 + Element Plus）

- 新建 `frontend/src/components/RedditCampaigns.vue`：对齐 RedditOperations.vue tab 风格
  - 列表 el-table：状态 tag、今日 posts/comments/upvotes 进度、启停按钮
  - 创建/编辑 el-dialog：el-date-picker daterange、时段 el-time-picker、配额 el-input-number、账号 el-select multiple、模式 el-radio
  - 执行记录 el-drawer：类型/状态/计划时间/结果/错误 + retry
- 挂载：RedditOperations.vue 新增 `<el-tab-pane label="自动化 Campaign" name="campaigns">`
- `frontend/src/api/reddit.ts` 追加：listCampaigns/createCampaign/updateCampaign/startCampaign/pauseCampaign/deleteCampaign/listCampaignActions/retryCampaignAction（风格同 scheduleRedditPost）

## 5. 文件清单

| 操作 | 文件 |
|---|---|
| 新建 | backend/alembic/versions/r016_reddit_campaign.py |
| 新建 | backend/app/services/reddit_campaign.py |
| 新建 | backend/app/tasks/reddit_campaign_tasks.py |
| 新建 | frontend/src/components/RedditCampaigns.vue |
| 修改 | backend/app/models/reddit.py、app/models/__init__.py |
| 修改 | backend/app/schemas/reddit.py、app/api/v1/reddit.py |
| 修改 | backend/app/core/celery_app.py（beat + include） |
| 修改 | backend/app/services/reddit_content.py（抽出帖子生成核心） |
| 修改 | frontend/src/api/reddit.ts、src/components/RedditOperations.vue |
| 测试 | backend/tests/test_reddit_campaign.py、test_reddit_vote_log.py |

## 6. 验证

pytest（fixture 沿用 conftest.py，参考 test_reddit_publish_enqueue.py / test_reddit_discover.py）：
- 配额统计正确；账号 warning/超限 → action skipped+reason
- planned_at 落在活跃时段、同账号间隔 >=25 分钟
- unique(campaign_id, dedup_key) 防重复计划；unique(account, thing) + upsert
- auto 产物 approved+scheduled_at 可被现有发布链消费；review 产物 pending_review
- execute_campaign_action claim 原子性、软超时→failed、僵尸回收
- upvote_self 强制 voter != author

端到端：`alembic upgrade head`（conda env DNF）→ 建 1 小时窗口、配额 1 的 campaign → 观察 beat tick 日志与 social 队列 → RedditOperations 新 tab 查看执行记录 → 验证 auto 内容按点自动发布、review 内容出现在待审队列、点赞落 RedditVoteLog → pause 后无新动作。
