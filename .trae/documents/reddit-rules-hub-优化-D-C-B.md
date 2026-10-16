# Reddit 平台新规应对：存活监控(D) + 行为门控(C) + 版规意图合规(B)

## Context

Reddit 2026-08 更新后，Karma 硬门槛被 Rules Hub（LLM 意图识别审核）取代。平台需要三处防御/进化：
- **D 内容存活监控**：AI 审核时代内容会被静默移除，当前 `sync_reddit_post_metrics` 拿不到帖子就只记日志，无法区分"没人看"和"被删了"
- **C 养号阶段行为门控**：`ensure_karma_stage_consistency` 纯按 Karma 阈值推进阶段，"刷 Karma"的机械行为本身成为 AI 审核的异常信号
- **B 版规意图级合规**：版规仅存原文（rules_text）+ 手填备忘，需要 LLM 编译成意图级清单，生成前注入、生成后核对、发布前拦截

实施顺序：D → C → B（C 依赖 D 的移除数据；B 独立）。共一个迁移 r020。

## 已核实的关键事实（设计依据）

1. `ZernioClient.get_post_by_id`（zernio_client.py L340-381）内部 `except ZernioError: found=None` —— 429/超时/5xx 全部静默变 None，是存活误判最大来源；返回 dict 仅 8 个白名单字段（title/url/thing_id/subreddit/score/num_comments/created_utc/body），唯一 removed 痕迹是 `body=="[removed]"/"[deleted]"`
2. 评论无单条直查 API（仅 `list_post_comments` limit≤15），评论存活检查**不做**（列预留）
3. `reddit_account_health_check` 任务已注册但**无 beat 调度**（celery_app.py L81-83），需加回
4. `community_rules` 参数全部调用方汇聚于 `_rules_text_for_subreddit`（api/v1/reddit.py L183-191），一处注入全链路生效
5. 发布 gate：`publish_post_now`/`publish_comment_now` + campaign 两处都走 `check_post_publish`/`check_comment_publish`，加在这两个函数三路全覆盖
6. `RedditCommunity.rules_text` 仅能经 `apply_rules_to_community` 写入且同步更新 `rules_fetched_at` → 用 `rules_fetched_at > rules_manifest_at` 判断需重编译，无需 hash 列

## 一、迁移 r020（`alembic/versions/r020_lifecycle_and_rules_manifest.py`，down_revision="r019_content_feedback"）

| 表 | 新列 |
|---|---|
| reddit_posts | `upstream_removed` Bool(false)、`upstream_removed_at` TS null、`upstream_removed_reason` Str(100) null、`upstream_missing_streak` Int(0)、`intent_risk` Str(20) null、`intent_reasons` JSON null |
| reddit_comments | 同上 6 列一次到位（v1 不检查评论存活，列备用） |
| reddit_communities | `rules_manifest` JSON null、`rules_manifest_at` TS null |

模型同步：`app/models/reddit.py` RedditPost（L31-77）、RedditComment（L80-127）、RedditCommunity（L170-222）。不建新索引（账号维度基数小，现有 account_id 索引足够）。

## 二、功能 D：内容存活监控

### D1 严格版存活 API（区分 找到/消失/异常）
- `app/services/zernio_client.py`：`get_post_by_id_checked(...) -> tuple[str, dict|None]`，返回 `found | removed_hint | not_found | error`。与现 get_post_by_id 同样的两次 search_posts 精确匹配，但 **ZernioError 直接返回 error 不吞**；命中且 body∈{"[removed]","[deleted]"} → removed_hint。现 get_post_by_id 不动。
- `app/services/reddit_client.py`：`RedditApiClient.check_post_alive(thing_id, subreddit)` 包装（ZernioError → ("error", None) 不抛出）。

### D2 sync_reddit_post_metrics 改造（`app/tasks/reddit_tasks.py` L357-435）
并入每日 sync（不建独立任务，同数据源同 client）。常量：`UPSTREAM_MISSING_CONFIRM_ROUNDS=2`（连续 2 天缺失才标记）。逻辑：
- `error` → 不计连缺、不动指标，errors+1
- `removed_hint` → 幂等标记 `_mark_upstream_removed(db, post, "body_removed_marker")`，不再写快照
- `not_found` → streak+1，达 2 → `_mark_upstream_removed(db, post, "upstream_gone")`
- `found` → streak 清零；若原已标记 upstream_removed → **自愈清除**（索引波动防误判）；原有快照逻辑（prev score 回落告警 + RedditPostMetric）不变
- `_mark_upstream_removed`：置 upstream_removed/removed_at/reason + error_message 留痕 + warning 日志
- 返回值扩展 `{"synced","missing","errors","removed","recovered"}`

## 三、功能 C：养号阶段行为门控

### C1 统计与阈值（`app/services/reddit_risk.py`）
常量：`BEHAVIOR_WINDOW_DAYS=30`、`REMOVAL_RATE_LIMIT=0.20`（冻结进阶）、`REMOVAL_RATE_DOWNGRADE=0.40`（降级+warning）、`REMOVAL_MIN_SAMPLE=5`、`REMOVAL_ABS_MIN=3`（小样本保护）、`DIVERSITY_MIN_COMMUNITIES=2`（ready→active 门槛）、`_STAGE_DOWNGRADE` 映射。

`get_account_behavior_stats(db, site_id, account_id, *, days=30) -> dict`：SQL 聚合（count + distinct subreddit，禁全表），返回 posts_total/posts_removed/removal_rate(None=样本不足)/community_count。

### C2 门控重构（向后兼容）
- `ensure_karma_stage_consistency(db, profile, *, behavior=None)`：加行为门 `_behavior_gates_pass` —— ① risk_status=warning 冻结 ② removal_rate>20% 冻结 ③ ready→active 需社区数≥2；通过后原 Karma 三段推进原样保留
- `ensure_behavior_alerts(db, profile, stats) -> str|None`：全角色通用，removal_rate>40% → warmup 降一级 + mark_warning（seeding/expert 仅告警）
- health check 任务（`app/tasks/reddit_tasks.py` L438+）：每 profile 先算 stats，传入门控，再跑 alerts；返回值加 downgraded

### C3 调度恢复 + 前端
- beat 加回：`reddit-account-health-check` `crontab(hour=4, minute=7)`（时序链：03:47 sync 落存活标记 → 04:07 health check 读标记 → 04:17 expand → 04:37 calibration）
- `RedditAccountOut` 加 `removal_rate_30d/removed_count_30d/recent_posts_30d`；`_accounts_out`（api/v1/reddit.py L594-634）循环外两条 group-by 聚合防 N+1
- 前端 `RedditOperations.vue` 账号表格加"30天移除率"徽标列（>20% warning 色 / >40% danger 色，tooltip 显示明细）

## 四、功能 B：版规意图级合规

### B1 新文件 `app/services/reddit_intent.py`
- `compile_rules_manifest(rules_text, ai) -> dict|None`：LLM 编译为 `{"allow_links": bool, "promo_policy": str, "banned_intents": [str], "required_style": [str], "notes": [str]}`；空文本/失败/解析失败 → None（fail-open 不阻断）；banned_intents 截断至 8
- `is_manifest_stale(row) -> bool`：manifest 缺失或 rules_fetched_at > rules_manifest_at（时区归一参照 is_rules_fresh 的 aware 处理）
- `format_manifest_for_prompt(manifest) -> str`：人类可读注入行
- `check_intent_compliance(body, manifest, ai, *, content_type) -> IntentComplianceResult(risk: ok|violation|unknown, reasons)`：无 manifest/超短内容(<60字) → ok 免 LLM；LLM 失败 → unknown 不阻断

### B2 编译挂接（`app/services/reddit_community_verify.py` L715-725）
`apply_rules_to_community(row, result, *, ai=None)`：写完 rules_text 且 ai 非 None → 编译落库（延迟 import 防循环）。三调用点透传 ai：`refresh_community_existence_and_rules` 加 ai 参数（API refresh-community-rules 端点传 get_ai_client()）、`expand_similar_communities`（已有 ai）、`create_community` 端点。

### B3 生成期注入
`_rules_text_for_subreddit`（api/v1/reddit.py L183-191）返回前拼接 `format_manifest_for_prompt(row.rules_manifest)` → 评论/帖子/campaign 全链路自动获得。

### B4 管线内意图核对（`app/services/reddit_content.py`）
- `GeneratedComment`/`GeneratedPost` 加 `intent_risk="ok"`、`intent_reasons=[]`；两 pipeline 签名加 `manifest: dict|None=None`
- comment 管线：品牌闸同层插入 intent 检查，violation → `build_intent_revision_notes(reasons)` + `risk="intent_violation"` + continue（复用品牌闸重写路径）；2 轮耗尽仍 violation → 兜底
- post 管线：verdict 判定前同构插入
- `generate_post_content`（L416-431）传 manifest（延迟 import `_manifest_for_subreddit` 加进 L334-339 import 列表）

### B5 落库 + 发布 gate（无二次 LLM）
- 落库三处：评论 `_generate_comment_for_url`（reddit.py L1172-1188）、API 帖 `generate_post`（L815-829）、campaign `_create_campaign_post`（reddit_campaign.py L482-496，且 L510 降级链追加 intent_risk=="violation" 判定）
- gate：`check_post_publish`/`check_comment_publish` 加可选参数 `intent_risk/intent_reasons`，=="violation" → errors 拦截（历史数据 None 不拦）；`publish_post_now`/`publish_comment_now` + campaign 两处传已落库值
- schema：RedditPostOut/RedditCommentOut 加 intent_risk/intent_reasons；RedditCommunityOut 加 rules_manifest/rules_manifest_at

### B6 成本
- manifest 编译：仅版规更新时 1 次/社区（低频，不计限额）
- 意图核对：每次生成 +1 次 ai.chat（短输出）；campaign 路径在现有 `_ai_budget_consume` 调用处（reddit_campaign.py L1198/L1241）各 +1，限额 40 不变；API 路径不新增预算

## 五、测试计划（backend/tests/，MagicMock/monkeypatch；Celery 任务直接 .run() 前预取 ID）

- **新增 `test_reddit_lifecycle.py`**：get_post_by_id_checked 四态；sync 五用例（连缺2轮标记/error 保 streak/removed_hint 直标/found 清 streak/自愈清除）
- **新增 `test_reddit_behavior_gate.py`**：stats 聚合与小样本 None；门控矩阵（300 karma+25% 移除→冻结 / 10%→推进 / warning 冻结 / 社区数1→ready 冻结）；降级（45%→warmup 降一级+warning；seeding 仅 warning）；不传 behavior 向后兼容
- **新增 `test_reddit_intent_compliance.py`**：compile 四态；is_manifest_stale 时区混合；check_intent_compliance 五态；管线集成（恒 violation → 2 轮后 intent_risk=intent_violation）；gate 拦截/放行
- **回归**：test_reddit_content_pipeline / test_reddit_community_verify / test_reddit_community_rules / test_reddit_client_mock

## 六、风险与不做边界

**风险**：① 现吞 ZernioError 的误判已用"error 不计连缺 + 连续2轮确认 + found 自愈"三层防护 ② `refresh_community_existence_and_rules` 加参需 grep 全部调用方 ③ 连续超标日刷新 warning 时间戳不连降级（v1 简化）④ fail-open 保证 LLM 抖动不阻塞主链路

**不做**：评论存活检查（API 缺失，列预留）；独立存活 beat 任务；removed 原因细分；manifest hash 列；API 生成路径新增预算；发布时二次 LLM 核对（人工编辑后由人工审核兜底）；stage 自动恢复（降级后靠 Karma 重新走门控）；manifest 前端展示页

## 七、验证

1. `conda run -n SEO python -m pytest backend/tests/ -q`（至少 persona/lifecycle/behavior/intent/content_pipeline/community 相关）
2. `conda run -n SEO alembic upgrade head` 本地升级到 r020
3. 前端 `npm run build` 通过
4. 手动冒烟：账号矩阵页可见移除率列；生成一条评论观察 intent_risk 落库；强制 manifest 违规内容走审批确认被发布 gate 拦截
