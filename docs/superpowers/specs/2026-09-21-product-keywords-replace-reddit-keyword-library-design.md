# 产品关键词替换 Reddit 关键词库

日期：2026-09-21

## 目标

取消社交分发页的独立「关键词库」，改为在品牌/产品库中为每个产品绑定相关关键词；智能发现进入产品绑定社区时，仅用这些关键词搜索与过滤帖子。

## 决策摘要

| 项 | 选择 |
|----|------|
| 搜索词来源 | 仅产品关键词（不用名称 / 品类 / 卖点兜底） |
| 旧 `reddit_keywords` | 表 + API + UI 全部删除，不迁移 |
| 存储 | 独立表 `reddit_product_keywords` |
| 选词策略 | 无 priority；社区 search 时从该产品关键词中随机选 1 个 |
| 相关性过滤 | 使用该产品全部关键词 |

## 数据

### 新增 `reddit_product_keywords`

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | int PK | |
| `product_id` | FK → `reddit_products.id` ON DELETE CASCADE | |
| `keyword` | String(200) NOT NULL | trim 后非空 |
| `created_at` | timestamptz | |

约束：

- `(product_id, lower(keyword))` 唯一（或应用层规范化后唯一）
- 产品删除时级联删除关键词行

### 删除

- 表 `reddit_keywords`
- 模型 `RedditKeyword`、schema `RedditKeyword*`、站点级清理中的相关逻辑
- 发帖生成时对词库 `used_count` / `last_used_at` 的累计

排名监控用的站点级 `keywords` 表（SERP）不变。

## API

产品 create / update / out 增加 `keywords: string[]`：

- 写入：整表替换该产品下的关键词行（trim、去空、去重；建议上限约 20）
- 读出：按 `id` 或创建顺序返回字符串列表
- 不新增独立 `/reddit/keywords` 或 `/products/{id}/keywords` 路由

删除路由：

- `GET/POST /reddit/keywords`
- `PATCH/DELETE /reddit/keywords/{id}`

## 智能发现

改 `product_search_terms` / `smart_discover_for_account`：

1. 选中产品时加载其 `reddit_product_keywords`
2. 有词：随机选 1 个作为该次社区 `search` 的 keyword；全部词传入 `prefer_product_relevant`
3. 无词：不进行产品向搜索（`product_terms` 为空）；前端提示先绑定关键词

卖点 `talking_points` 仍只用于评论文案生成，不参与搜索。

## UI

### 品牌/产品库（`BrandProducts.vue`）

编辑产品弹窗增加「关键词（逗号分隔）」；保存时随产品 payload 提交 `keywords`。产品标签可显示词数量（可选）。

### 社交分发（`RedditOperations.vue`）

- 移除「关键词库」Tab、弹窗、列表与 API 调用
- 发帖表单关键词仍可手填；去掉「用于发帖」从词库填入
- 「高级：关键词搜索」保留（手动输入，与产品词无关）

## 非目标

- 不迁移旧 `reddit_keywords` 数据到产品
- 不恢复 used_count / priority / SEO vs AI 热搜分类
- 不改发帖强制绑定产品关键词
- 不改 SERP 排名关键词库

## 验收

1. 社交分发无「关键词库」Tab；调用旧 `/reddit/keywords` 返回 404
2. 产品可增改关键词；刷新后仍在
3. 选产品 + 绑定社区智能发现时，搜索词来自产品关键词（随机其一），且不用名称/品类/卖点
4. 产品无关键词时，产品向发现不产生有效搜索结果（或明确提示）
5. 删除产品后其关键词行一并消失
6. 排名监控关键词功能不受影响
