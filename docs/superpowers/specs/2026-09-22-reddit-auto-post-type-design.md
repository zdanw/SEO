# Reddit 发帖「自主发挥」设计

## 目标

固定五类帖型（踩坑/树洞/反常识/干货/求助）偏生硬。默认改为 LLM 按目标社区自主选择写法；需要时仍可选手动类型作提示。人设闲聊与产品推广同一套逻辑。

## 决策摘要

- 方案：新增 `post_type=auto` + 一条社区驱动通用 prompt；五类模板保留为可选提示。
- 产品意图与类型解耦：父开关「允许提及产品」；其下可选「带站点 URL」。
- 关产品 → `intent=casual`；开产品 → 必须选产品，`intent=promo`（计入 90/10）。

## 交互

1. **写法**：默认「自主发挥」；可选手动五类之一。
2. **产品提及**（与写法无关）：
   - 关：不注入产品 brief、不带链、不要求选产品。
   - 开：展示品牌/产品选择 + 「带站点 URL」子选项。
3. 审核列表类型列：`auto` 显示为「自主」。

## API / 数据

- `PostType` 增加 `"auto"`；库字段仍为 `varchar(20)`，无需迁移。
- `RedditPostGenerateIn` 增加 `allow_product: bool = False`。
- `include_site_url` 仅在 `allow_product=True` 时生效。
- `resolve_post_intent`：由 `allow_product` 决定（True→promo，False→casual）；不再用 vent/help_seek 强制 casual，也不再仅靠社区 purpose。

## Prompt

- 新增 `REDDIT_AUTO_PROMPT`：要求贴合 `r/{subreddit}` 常见体裁，自选叙事角度（故事/吐槽/提问/观点/求助等），禁止广告腔。
- `auto` 的标题风格从混合池随机抽，不绑定五类。
- 五类模板中写死的「禁止提产品」改为 `{product_rule}`，由 `allow_product` 注入。
- `generate_reddit_post(..., allow_product: bool)`：brief / site_line 只跟 `allow_product` 与 `site_url` 走。

## 非目标

- 不删除五类类型与历史数据。
- 不改评论生成流程（评论已有独立 intent）。
- 不改社区库 / 配额算法本身。

## 成功标准

- 默认生成可不选手动类型，产出贴合社区的英文帖。
- 开「允许提及产品」并选产品后，`content_intent=promo` 且 brief 进入 prompt。
- 关产品时 prompt 明确禁止品牌/链接。
- 选手动类型行为与原先类似，但产品规则跟开关而非类型绑定。
- 相关单测通过。
