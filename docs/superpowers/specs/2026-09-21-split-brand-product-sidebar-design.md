# 品牌与产品分栏管理

日期：2026-09-21

## 目标

将侧边栏「品牌产品库」拆成「品牌」「产品」两个入口，分开管理。

## 决策

| 项 | 选择 |
|----|------|
| 产品列表 | 扁平表，含所属品牌列；新增时下拉选品牌 |
| 品牌页 | 纯品牌 CRUD；显示产品数量，不在品牌页改产品 |
| 产品列表 API | 新增 `GET /reddit/products`，响应含 `brand_name` |
| 创建/更新/删除产品 | 沿用现有 `POST /brands/{id}/products`、`PATCH/DELETE /products/{id}` |

## 路由与 UI

| 菜单 | 路径 | 页面 |
|------|------|------|
| 品牌 | `/brands` | `Brands.vue` |
| 产品 | `/products` | `Products.vue` |

- 删除 `BrandProducts.vue`
- 社交分发入口改为「管理产品」→ `/products`

## API

- `RedditProductOut` 增加 `brand_name: str`
- `GET /reddit/products`：本站全部产品，按品牌 id、产品 id 排序

## 非目标

- 不支持编辑时更换所属品牌
- 不做产品列表分页/筛选（当前规模不需要）
- 不改智能发现与发帖逻辑

## 验收

1. 侧边栏可见「品牌」「产品」两项，无「品牌产品库」
2. 品牌页可增删改品牌，显示产品数，无产品编辑入口
3. 产品页扁平列表含品牌名；无品牌时提示先建品牌
4. `GET /api/v1/reddit/products` 返回含 `brand_name` 的列表
5. 社交分发「管理产品」跳转 `/products`
