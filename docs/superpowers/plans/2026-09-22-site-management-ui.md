# 站点管理 UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 前端站点 CRUD + 顶栏会话内切换，业务请求自动带 `X-Site-Id`。

**Architecture:** Pinia `useSiteStore` 持有站点列表与 `currentSiteId`；axios 拦截器注入 header；`/sites` 管理页；业务页 `watch(currentSiteId)` 重载。

**Tech Stack:** Vue 3 + Pinia + Element Plus + axios；后端 `/api/v1/sites` 已有。

## Global Constraints

- 不做成员管理、CMS/sitemap 表单、`localStorage` 持久化当前站
- 表单字段仅：名称、域名、状态、行业、备注
- 刷新后不传 `X-Site-Id`，回默认站
- 不改后端 API（除非阻塞）

---

### Task 1: sites API + Pinia store + HTTP 拦截器

**Files:**
- Create: `frontend/src/api/sites.ts`
- Create: `frontend/src/stores/site.ts`
- Modify: `frontend/src/api/http.ts`
- Modify: `frontend/src/layout/DefaultLayout.vue`（logout 清空）

- [ ] **Step 1:** 实现 `sites.ts`（list/create/update/delete）与 `useSiteStore`（`sites`、`currentSiteId`、`currentSite`、`loadSites`、`setCurrentSite`、`clear`）
- [ ] **Step 2:** `http` 请求拦截器在 `currentSiteId` 有值时设置 `X-Site-Id`
- [ ] **Step 3:** logout 调用 `siteStore.clear()`

---

### Task 2: Sites 管理页 + 路由/侧栏

**Files:**
- Create: `frontend/src/views/Sites.vue`
- Modify: `frontend/src/router/index.ts`
- Modify: `frontend/src/layout/DefaultLayout.vue`

- [ ] **Step 1:** `Sites.vue` 表格 CRUD（精简字段）；删当前站则 `clear`
- [ ] **Step 2:** 路由 `/sites`；侧栏「工作台」加「站点」；subtitle

---

### Task 3: 顶栏站点切换器

**Files:**
- Modify: `frontend/src/layout/DefaultLayout.vue`

- [ ] **Step 1:** 顶栏 `el-select` 切换站点；onMounted `loadSites`；「管理站点」链到 `/sites`
- [ ] **Step 2:** 切换时 `setCurrentSite` + `ElMessage` 提示

---

### Task 4: 业务页 watch 当前站

**Files:**
- Modify: Dashboard / Brands / Products / SerpMonitor / SocialAccounts / SocialScheduler / Ops / RedditOperations

- [ ] **Step 1:** 各页对 `currentSiteId` 做 `watch`，变化时重新调用原有 load 函数

---

### Task 5: 手册 + 验收

**Files:**
- Modify: `使用手册.md`

- [ ] **Step 1:** 更新产品定位、页面表、1.4 站点隔离说明
- [ ] **Step 2:** 手动验收清单对照 spec
