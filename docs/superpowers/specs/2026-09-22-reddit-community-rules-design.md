# Reddit 社区存在性 + 版规拉取与生成遵守

## 目标

优化社区拉取规则：校验社区真实存在；拉取官方版规并展示；生成帖/评时把版规写入 prompt 要求遵守。硬限制仍用现有字段（如 `allows_links`）。

## 决策

- 方案：公开 Reddit API（`about.json` + `about/rules.json`）+ 落库 + prompt 注入
- 生成：版规原文/拼接文本注入 prompt（方案 A），不做 LLM 结构化抽取
- 展示：社区库 + 发帖/评论选中社区时旁路摘要（方案 B）
- 时机：创建时拉取；社区库「刷新版规」；24h 缓存，强制刷新可绕过

## 数据

| 字段 | 说明 |
|--|--|
| 现有 `exists` / `verified_at` / `verify_error` 等 | 存在性与活跃校验 |
| `rules_text` Text nullable | 官方规则拼接文本 |
| `rules_fetched_at` timestamptz nullable | 上次成功拉取 |
| `rules_note` | 人工备忘，不与官方版规互相覆盖 |

拼接格式示例：每条 `#{short_name}: {description}`，总长生成时截断约 2500 字符。

## API

- 创建社区：存在校验失败 → 400；版规拉取失败 → 仍可创建，`rules_text` 空，错误写入提示字段
- `POST /reddit/communities/{id}/refresh-rules`：强制重新校验存在 + 拉版规；不存在则 `is_active=false` 并返回错误信息
- `RedditCommunityOut` 增加 `rules_text`、`rules_fetched_at`

## UI

- 社区库：版规列（摘要 + tooltip/弹窗全文）；行操作「刷新版规」
- 发帖选社区、评论智能发现选社区：选中后显示版规摘要（无版规时提示「尚未拉取，可到社区库刷新」）

## 生成注入

- `generate_reddit_post` / `generate_reddit_comment`：若社区库有该 subreddit 的 `rules_text`，追加：
  - `Subreddit rules (must follow; if conflict with other instructions, rules win): ...`
- 发布路径现有 `reddit_risk`（禁外链等）不变

## 非目标

- 不用 Zernio 拉版规
- 不做版规 LLM 结构化抽取自动改 `allows_links`
- 不改评论智能发现的搜帖关键词逻辑

## 成功标准

- 新建真实社区可看到 `rules_text`（Reddit 有公开规则时）
- 假社区创建被拒或标记不存在
- 刷新版规可更新文本与时间戳
- 生成帖/评的 prompt 含版规片段（有数据时）
- 相关单测通过
