# Zernio Key 按站点隔离

## Goal

Zernio API Key 与 Reddit 账号、品牌等一致，按 `site_id` 隔离；顶栏切站后只看到/操作当前站的 Key。

## Decision

- `zernio_api_keys` 增加非空 `site_id`（FK → `client_sites.id`）
- 唯一约束：`(site_id, api_key)`（站内唯一；跨站允许相同 Key 字符串）
- 迁移：现有全局 Key 全部归到**最早创建的站点**（默认站）；其它站需自行添加
- CRUD / 同步 / `is_zernio_ready` / 孤儿账号清理：一律按当前 `site_id` 过滤
- 删站时级联删除该站 Key
- 不做跨站复制/引用

## Out of scope

- Reddit 账号隔离模型（已按站）
- 跨站共享 Key UI
