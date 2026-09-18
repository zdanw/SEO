# 品牌/产品库设计（替换产品简报）

日期：2026-09-18

## 目标

用「品牌 → 多产品」库替换站点级单一产品简报。仅约 10% 产品向**评论**生成时必选品牌+产品，内容向该产品靠拢。

## 数据

- `reddit_brands`: site_id, name, is_active
- `reddit_products`: brand_id, name, category, talking_points(JSON), is_active
- `reddit_comments` 增加可空 `brand_id`, `product_id`（promo 时写入）
- 迁移旧 `reddit_product_briefs` → 每站点一个品牌+一个产品后删除旧表

## API

- CRUD `/reddit/brands`, `/reddit/brands/{id}/products`
- 评论生成 / 智能发现 promo：请求带 `brand_id` + `product_id`
- 移除 `/reddit/product-brief`

## UI

- 顶部「产品简报」改为「品牌/产品库」
- 评论智能发现与 URL 生成：在产品向路径展示品牌→产品选择器

## 非目标

- 发帖生成不强制选品牌/产品
- 竞品、禁止宣称字段本期不做
