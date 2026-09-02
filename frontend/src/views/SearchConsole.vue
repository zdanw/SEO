<template>
  <div class="page-container">
    <!-- 连接区 -->
    <el-card shadow="never" class="content-card" style="margin-bottom: 16px">
      <template #header><span style="font-weight: 600">Google 账号连接</span></template>

      <el-alert
        v-if="!status.configured"
        type="warning"
        show-icon
        :closable="false"
        title="后端未配置 Google OAuth"
        description="请在 backend/.env 中设置 GOOGLE_CLIENT_ID 和 GOOGLE_CLIENT_SECRET，并在 Google Cloud Console 启用 Search Console API。"
        style="margin-bottom: 16px"
      />

      <div v-if="!status.connected" class="connect-panel">
        <p class="connect-hint">
          连接 Google Search Console 后，可查看客户网站在 Google 搜索中的真实点击、展示与排名数据。
        </p>
        <el-button type="primary" :loading="connectLoading" :disabled="!status.configured" @click="handleConnect">
          连接 Google 账号
        </el-button>
      </div>

      <div v-else class="connected-panel">
        <div class="connected-info">
          <el-tag type="success" size="small">已连接</el-tag>
          <span v-if="status.google_email" class="email">{{ status.google_email }}</span>
        </div>
        <div class="site-select-row">
          <span class="label">监控站点</span>
          <el-select
            v-model="selectedSite"
            placeholder="选择 GSC 属性"
            filterable
            style="min-width: 320px; flex: 1"
            :loading="sitesLoading"
            @change="handleSiteChange"
          >
            <el-option
              v-for="site in sites"
              :key="site.site_url"
              :label="site.site_url"
              :value="site.site_url"
            />
          </el-select>
          <el-button @click="loadSites">刷新站点</el-button>
          <el-popconfirm title="确认断开 Google 连接？" @confirm="handleDisconnect">
            <template #reference>
              <el-button type="danger" plain>断开连接</el-button>
            </template>
          </el-popconfirm>
        </div>
      </div>
    </el-card>

    <!-- 数据区 -->
    <template v-if="status.connected && status.site_url">
      <div class="toolbar-row" style="margin-bottom: 16px">
        <el-select v-model="days" style="width: 120px" @change="loadAnalytics">
          <el-option :value="7" label="近 7 天" />
          <el-option :value="28" label="近 28 天" />
          <el-option :value="90" label="近 90 天" />
        </el-select>
        <span v-if="summary" class="date-range">
          数据区间：{{ summary.start_date }} ~ {{ summary.end_date }}
          <el-tooltip content="GSC 数据通常延迟 2~3 天，结束日期已自动扣除">
            <el-icon style="vertical-align: -2px; margin-left: 4px"><QuestionFilled /></el-icon>
          </el-tooltip>
        </span>
        <el-button style="margin-left: auto" @click="loadAnalytics">刷新数据</el-button>
      </div>

      <el-row :gutter="16" class="stat-card-grid" style="margin-bottom: 16px">
        <el-col :xs="12" :sm="6" class="stat-col">
          <el-card shadow="never" class="stat-card">
            <div class="label">总点击</div>
            <div class="value">{{ formatNum(summary?.clicks) }}</div>
          </el-card>
        </el-col>
        <el-col :xs="12" :sm="6" class="stat-col">
          <el-card shadow="never" class="stat-card">
            <div class="label">总展示</div>
            <div class="value">{{ formatNum(summary?.impressions) }}</div>
          </el-card>
        </el-col>
        <el-col :xs="12" :sm="6" class="stat-col">
          <el-card shadow="never" class="stat-card">
            <div class="label">平均 CTR</div>
            <div class="value">{{ formatPct(summary?.ctr) }}</div>
          </el-card>
        </el-col>
        <el-col :xs="12" :sm="6" class="stat-col">
          <el-card shadow="never" class="stat-card">
            <div class="label">平均排名</div>
            <div class="value">{{ formatPos(summary?.position) }}</div>
          </el-card>
        </el-col>
      </el-row>

      <el-card shadow="never" class="content-card">
        <el-tabs v-model="activeTab" @tab-change="loadTable">
          <el-tab-pane label="按关键词" name="query" />
          <el-tab-pane label="按页面" name="page" />
        </el-tabs>
        <el-table :data="rows" v-loading="tableLoading" stripe max-height="480">
          <el-table-column
            :prop="'key'"
            :label="activeTab === 'query' ? '搜索词' : '页面 URL'"
            min-width="280"
            show-overflow-tooltip
          />
          <el-table-column prop="clicks" label="点击" width="90" align="right" sortable />
          <el-table-column prop="impressions" label="展示" width="100" align="right" sortable />
          <el-table-column label="CTR" width="90" align="right" sortable :sort-method="sortCtr">
            <template #default="{ row }">{{ formatPct(row.ctr) }}</template>
          </el-table-column>
          <el-table-column prop="position" label="平均排名" width="110" align="right" sortable>
            <template #default="{ row }">{{ formatPos(row.position) }}</template>
          </el-table-column>
        </el-table>
      </el-card>
    </template>

    <el-card v-else-if="status.connected && !status.site_url" shadow="never" class="content-card">
      <el-empty description="请在上方选择一个 Search Console 站点以查看数据" />
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { QuestionFilled } from '@element-plus/icons-vue'
import {
  getGscStatus,
  startGscOAuth,
  disconnectGsc,
  listGscSites,
  selectGscSite,
  getGscSummary,
  getGscAnalytics,
  type GscStatus,
  type GscSite,
  type GscSummary,
  type GscAnalyticsRow,
} from '@/api/gsc'

const route = useRoute()
const router = useRouter()

const status = ref<GscStatus>({ configured: false, connected: false })
const sites = ref<GscSite[]>([])
const selectedSite = ref('')
const days = ref(28)
const summary = ref<GscSummary | null>(null)
const rows = ref<GscAnalyticsRow[]>([])
const activeTab = ref<'query' | 'page'>('query')

const connectLoading = ref(false)
const sitesLoading = ref(false)
const tableLoading = ref(false)

function formatNum(v?: number | null): string {
  if (v == null) return '—'
  return v.toLocaleString('zh-CN')
}

function formatPct(v?: number | null): string {
  if (v == null) return '—'
  return `${(v * 100).toFixed(2)}%`
}

function formatPos(v?: number | null): string {
  if (v == null || v === 0) return '—'
  return v.toFixed(1)
}

function sortCtr(a: GscAnalyticsRow, b: GscAnalyticsRow): number {
  return a.ctr - b.ctr
}

async function loadStatus() {
  status.value = await getGscStatus()
  selectedSite.value = status.value.site_url || ''
}

async function loadSites() {
  if (!status.value.connected) return
  sitesLoading.value = true
  try {
    sites.value = await listGscSites()
  } finally {
    sitesLoading.value = false
  }
}

async function loadAnalytics() {
  if (!status.value.connected || !status.value.site_url) return
  tableLoading.value = true
  try {
    const [sum, detail] = await Promise.all([
      getGscSummary(days.value),
      getGscAnalytics({ days: days.value, dimension: activeTab.value, row_limit: 200 }),
    ])
    summary.value = sum
    rows.value = detail.rows
  } finally {
    tableLoading.value = false
  }
}

async function loadTable() {
  if (!status.value.site_url) return
  tableLoading.value = true
  try {
    const detail = await getGscAnalytics({
      days: days.value,
      dimension: activeTab.value,
      row_limit: 200,
    })
    rows.value = detail.rows
  } finally {
    tableLoading.value = false
  }
}

async function handleConnect() {
  connectLoading.value = true
  try {
    const { auth_url } = await startGscOAuth()
    window.location.href = auth_url
  } finally {
    connectLoading.value = false
  }
}

async function handleSiteChange(siteUrl: string) {
  if (!siteUrl) return
  await selectGscSite(siteUrl)
  ElMessage.success('站点已切换')
  await loadStatus()
  await loadAnalytics()
}

async function handleDisconnect() {
  await disconnectGsc()
  status.value = { configured: status.value.configured, connected: false }
  sites.value = []
  selectedSite.value = ''
  summary.value = null
  rows.value = []
  ElMessage.success('已断开 Google 连接')
}

function handleOAuthCallback() {
  const gsc = route.query.gsc as string | undefined
  if (!gsc) return
  if (gsc === 'connected') {
    ElMessage.success('Google Search Console 连接成功')
  } else if (gsc === 'error') {
    const msg = (route.query.message as string) || '授权失败'
    ElMessage.error(`Google 授权失败：${msg}`)
  }
  router.replace({ path: '/search-console' })
}

onMounted(async () => {
  handleOAuthCallback()
  await loadStatus()
  if (status.value.connected) {
    await loadSites()
    if (status.value.site_url) {
      await loadAnalytics()
    }
  }
})
</script>

<style scoped lang="scss">
.connect-panel {
  .connect-hint {
    color: #606266;
    margin: 0 0 16px;
    line-height: 1.6;
  }
}

.connected-panel {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.connected-info {
  display: flex;
  align-items: center;
  gap: 12px;

  .email {
    color: #606266;
    font-size: 14px;
  }
}

.site-select-row {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;

  .label {
    font-size: 14px;
    color: #606266;
    flex-shrink: 0;
  }
}

.toolbar-row {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}

.date-range {
  font-size: 13px;
  color: #909399;
}
</style>
