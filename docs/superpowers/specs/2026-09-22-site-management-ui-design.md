# 站点管理 UI 与会话切换

日期：2026-09-22

## 目标

补齐站点管理前端，并支持顶栏切换当前客户站；业务请求按当前站隔离。刷新后回到默认站。

## 决策

| 项 | 选择 |
|----|------|
| 范围 | CRUD + 全局站点切换；不做成员管理 |
| 当前站记忆 | 仅内存（会话内）；刷新后不传 `X-Site-Id`，后端用默认站 |
| 表单字段 | 名称、域名、状态、行业、备注（不做 CMS/sitemap） |
| 实现方式 | Pinia store + axios 拦截器注入 `X-Site-Id` |
| 切换刷新 | 业务页 `watch(currentSiteId)` 重新拉数；不整页 reload |

## 架构

- `frontend/src/stores/site.ts`：`sites`、`currentSiteId`、`loadSites`、`setCurrentSite`、`clear`
- `frontend/src/api/http.ts`：有 `currentSiteId` 时设置请求头 `X-Site-Id`
- `frontend/src/api/sites.ts`：对接 `/api/v1/sites` CRUD
- 退出登录时清空 store
- 后端 `/api/v1/sites` 保持不变

## 路由与 UI

| 菜单 | 路径 | 页面 |
|------|------|------|
| 站点 | `/sites` | `Sites.vue` |

- 顶栏：站点下拉（名称 · 域名）+「管理站点」→ `/sites`
- 侧栏「工作台」增加「站点」
- `/sites`：表格 CRUD；删除二次确认；若删除的是当前站则 `clear` 当前选择（回默认站）

## 受切换影响的页面

切换站点后需重新加载数据：

- Dashboard、Brands、Products、SerpMonitor
- SocialScheduler / RedditOperations、SocialAccounts、Ops

## 非目标

- 站点成员邀请/改角色
- CMS、sitemap、API Key 表单
- `localStorage` / 服务端持久化当前站
- 后端 API 变更（除非发现阻塞 bug）

## 文档

更新 `使用手册.md`：站点隔离从「透明默认」改为「可切换；刷新回默认」。

## 验收

1. 可增删改站点（精简字段）
2. 顶栏可切换；对应请求带 `X-Site-Id`
3. 切换后业务列表随站点变化
4. 刷新后无 `X-Site-Id`，回到默认站数据
5. 手册已更新
