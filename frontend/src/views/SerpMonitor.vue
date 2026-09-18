<template>
  <div class="page-container">
    <div class="page-header page-header--actions">
      <el-button type="primary" @click="openKeywordDialog">添加关键词</el-button>
    </div>

    <el-tabs v-model="activeTab" type="border-card" class="page-tabs">
      <!-- 排名趋势 -->
      <el-tab-pane label="排名趋势" name="trends">
        <div class="filter-bar">
          <el-select v-model="selectedKeywordIds" multiple placeholder="选择关键词" filterable style="min-width: 300px" @change="loadTrends">
            <el-option v-for="k in keywords" :key="k.id" :label="k.keyword" :value="k.id" />
          </el-select>
          <el-select v-model="trendDays" style="width: 120px" @change="loadTrends">
            <el-option :value="7" label="近 7 天" />
            <el-option :value="30" label="近 30 天" />
            <el-option :value="90" label="近 90 天" />
          </el-select>
          <el-button type="primary" :loading="trendLoading" @click="loadTrends">刷新</el-button>
        </div>
        <el-empty v-if="keywords.length === 0" description="暂无关键词，请先添加要监控的搜索词" />
        <div v-if="serpConfig?.mode === 'mock'" style="margin: 8px 0">
          <el-alert type="warning" :closable="false" title="当前为 Mock 模式（未配置 ScrapingBee / 代理池），排名数据为随机生成" />
        </div>
        <div v-else-if="serpConfig?.mode === 'scrapingbee'" style="margin: 8px 0">
          <el-alert type="success" :closable="false" title="ScrapingBee 已启用，排名数据来自 Google 实时抓取" />
        </div>
        <el-alert
          v-if="trendHint"
          type="info"
          :closable="false"
          :title="trendHint"
          style="margin: 8px 0"
        />
        <div ref="trendChartRef" style="height: 400px; margin-top: 12px"></div>
      </el-tab-pane>

      <!-- 最新排名 -->
      <el-tab-pane label="最新排名" name="latest">
        <div class="filter-bar">
          <el-button @click="loadLatest">刷新</el-button>
        </div>
        <el-empty v-if="!latestLoading && latestRanks.length === 0" description="暂无关键词，点击右上角「添加关键词」开始监控">
          <el-button type="primary" @click="openKeywordDialog">添加关键词</el-button>
        </el-empty>
        <el-table v-else :data="latestRanks" v-loading="latestLoading" stripe style="margin-top: 12px">
          <el-table-column type="index" label="序号" width="60" />
          <el-table-column prop="keyword" label="关键词" min-width="200" />
          <el-table-column prop="target_url" label="落地页" min-width="200" show-overflow-tooltip />
          <el-table-column prop="rank" label="排名" width="100" align="center">
            <template #default="{ row }">
              <el-tag v-if="row.rank" :type="rankTagType(row.rank)" effect="dark">{{ row.rank }}</el-tag>
              <span v-else style="color: #909399">—</span>
            </template>
          </el-table-column>
          <el-table-column prop="crawl_status" label="状态" width="100" align="center">
            <template #default="{ row }">
              <el-tag :type="statusTagType(row.crawl_status)" size="small">{{ row.crawl_status }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="last_crawled" label="最后抓取" width="180" />
          <el-table-column label="操作" width="200" fixed="right">
            <template #default="{ row }">
              <el-button size="small" type="success" :loading="crawlingId === row.keyword_id" @click="handleCrawl(row.keyword_id)">
                触发抓取
              </el-button>
              <el-popconfirm title="确认删除该关键词？" @confirm="handleDeleteKeyword(row.keyword_id)">
                <template #reference>
                  <el-button size="small" type="danger" link>删除</el-button>
                </template>
              </el-popconfirm>
            </template>
          </el-table-column>
        </el-table>
      </el-tab-pane>

      <!-- 快照记录 -->
      <el-tab-pane label="快照记录" name="snapshots">
        <div class="filter-bar">
          <el-select v-model="snapshotStatusFilter" placeholder="状态筛选" clearable style="width: 140px" @change="loadSnapshots">
            <el-option label="成功" value="success" />
            <el-option label="被封" value="blocked" />
            <el-option label="超时" value="timeout" />
            <el-option label="错误" value="error" />
          </el-select>
          <el-button @click="loadSnapshots">刷新</el-button>
        </div>
        <el-table :data="snapshots" v-loading="snapshotLoading" stripe style="margin-top: 12px">
          <el-table-column prop="time" label="时间" width="180" />
          <el-table-column label="序号" width="80" align="center">
            <template #default="{ row }">
              {{ keywordSeqMap.get(row.keyword_id) ?? '—' }}
            </template>
          </el-table-column>
          <el-table-column prop="rank" label="排名" width="80" align="center" />
          <el-table-column prop="page" label="页数" width="80" align="center" />
          <el-table-column prop="crawl_status" label="状态" width="100" align="center">
            <template #default="{ row }">
              <el-tag :type="statusTagType(row.crawl_status)" size="small">{{ row.crawl_status }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="proxy_used" label="代理" width="100" />
          <el-table-column prop="error_message" label="错误信息" min-width="200" show-overflow-tooltip />
        </el-table>
      </el-tab-pane>
    </el-tabs>

    <!-- 添加关键词对话框 -->
    <el-dialog v-model="keywordDialogVisible" title="添加关键词" width="480px">
      <el-form :model="keywordForm" label-width="90px">
        <el-form-item label="关键词" required>
          <el-input v-model="keywordForm.keyword" placeholder="例如：Python asyncio best practices" />
        </el-form-item>
        <el-form-item label="落地页 URL">
          <el-input v-model="keywordForm.target_url" placeholder="https://yoursite.com/article-slug" />
        </el-form-item>
        <el-form-item label="搜索地区">
          <el-select v-model="keywordForm.region" style="width: 100%">
            <el-option value="global" label="全球 (global)" />
            <el-option value="us" label="美国 (us)" />
            <el-option value="uk" label="英国 (uk)" />
            <el-option value="cn" label="中国 (cn)" />
            <el-option value="hk" label="香港 (hk)" />
          </el-select>
        </el-form-item>
        <el-form-item label="优先级">
          <el-slider v-model="keywordForm.priority" :min="1" :max="5" :step="1" show-stops />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="keywordDialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="keywordFormLoading" @click="handleCreateKeyword">保存并抓取</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, onActivated, nextTick, watch, onBeforeUnmount } from 'vue'
import * as echarts from 'echarts'
import { ElMessage } from 'element-plus'
import { listKeywords, createKeyword, deleteKeyword, type Keyword } from '@/api/keywords'
import {
  getLatestRanks,
  getTrends,
  getSerpConfig,
  listSnapshots,
  triggerCrawl,
  type LatestRank,
  type SerpConfig,
  type SerpRankSnapshot,
  type TrendsData,
} from '@/api/serp'
import { applyRankTrendChart, formatKeywordTrendLabel } from '@/utils/rankTrendChart'

const activeTab = ref('trends')

// 关键词列表
const keywords = ref<Keyword[]>([])
const keywordSeqMap = computed(() => {
  const map = new Map<number, number>()
  keywords.value.forEach((k, i) => map.set(k.id, i + 1))
  return map
})
const keywordDialogVisible = ref(false)
const keywordFormLoading = ref(false)
const keywordForm = ref({
  keyword: '',
  target_url: '',
  region: 'us',
  priority: 3,
})

function openKeywordDialog() {
  keywordForm.value = { keyword: '', target_url: '', region: 'us', priority: 3 }
  keywordDialogVisible.value = true
}

async function reloadKeywords() {
  keywords.value = await listKeywords()
}

async function handleCreateKeyword() {
  if (!keywordForm.value.keyword.trim()) {
    ElMessage.warning('请输入关键词')
    return
  }
  keywordFormLoading.value = true
  try {
    const created = await createKeyword({
      keyword: keywordForm.value.keyword.trim(),
      target_url: keywordForm.value.target_url.trim() || undefined,
      region: keywordForm.value.region,
      priority: keywordForm.value.priority,
    })
    ElMessage.success('关键词已添加，正在触发首次抓取')
    keywordDialogVisible.value = false
    await reloadKeywords()
    selectedKeywordIds.value = [...new Set([...selectedKeywordIds.value, created.id])]
    await loadLatest()
    await triggerCrawl(created.id)
    ElMessage.info('抓取任务已提交，约 10 秒后刷新查看最新排名')
  } finally {
    keywordFormLoading.value = false
  }
}

async function handleDeleteKeyword(keywordId: number) {
  await deleteKeyword(keywordId)
  ElMessage.success('已删除')
  selectedKeywordIds.value = selectedKeywordIds.value.filter((id) => id !== keywordId)
  await reloadKeywords()
  await loadLatest()
}

// ============ 趋势图 ============
const trendChartRef = ref<HTMLElement>()
let trendChart: echarts.ECharts | null = null
const selectedKeywordIds = ref<number[]>([])
const trendDays = ref(30)
const trendLoading = ref(false)
const serpConfig = ref<SerpConfig | null>(null)
const trendsData = ref<TrendsData>({})
const trendHint = ref('')

async function loadSerpConfig() {
  try {
    serpConfig.value = await getSerpConfig()
  } catch {
    serpConfig.value = null
  }
}

async function loadTrends() {
  if (selectedKeywordIds.value.length === 0) {
    ElMessage.warning('请先选择关键词')
    return
  }
  trendLoading.value = true
  try {
    const data = await getTrends(selectedKeywordIds.value, trendDays.value)
    trendsData.value = data
    renderTrendChart()
  } catch {
    // 错误已由 http 拦截器处理
  } finally {
    trendLoading.value = false
  }
}

function renderTrendChart() {
  if (!trendChartRef.value) return
  if (!trendChart) {
    trendChart = echarts.init(trendChartRef.value)
  }

  const kwById = new Map(keywords.value.map((k) => [k.id, k]))
  const seriesInput = selectedKeywordIds.value.map((kid) => {
    const kw = kwById.get(kid)
    return {
      name: kw ? formatKeywordTrendLabel(kw.keyword, kw.target_url) : `关键词 #${kid}`,
      points: trendsData.value[kid] || [],
    }
  })

  const meta = applyRankTrendChart(trendChart, seriesInput, { periodDays: trendDays.value })
  trendHint.value =
    meta.pointCount < 2
      ? '当前仅 1 个时间点，缩放条无效；请多次触发抓取后再查看趋势'
      : !meta.showDataZoom
        ? '时间跨度较短，已隐藏底部缩放条；可在图表内滚轮缩放'
        : ''
}

// ============ 最新排名 ============
const latestRanks = ref<LatestRank[]>([])
const latestLoading = ref(false)
const crawlingId = ref<number | null>(null)

async function loadLatest() {
  latestLoading.value = true
  try {
    latestRanks.value = await getLatestRanks()
  } finally {
    latestLoading.value = false
  }
}

async function handleCrawl(keywordId: number) {
  crawlingId.value = keywordId
  try {
    await triggerCrawl(keywordId)
    ElMessage.success('已触发抓取任务，请稍后刷新查看结果')
  } finally {
    crawlingId.value = null
  }
}

// ============ 快照记录 ============
const snapshots = ref<SerpRankSnapshot[]>([])
const snapshotLoading = ref(false)
const snapshotStatusFilter = ref('')

async function loadSnapshots() {
  snapshotLoading.value = true
  try {
    snapshots.value = await listSnapshots({
      crawl_status: snapshotStatusFilter.value || undefined,
      size: 100,
    })
  } finally {
    snapshotLoading.value = false
  }
}

// ============ 工具函数 ============
function rankTagType(rank: number): string {
  if (rank <= 3) return 'success'
  if (rank <= 10) return 'warning'
  if (rank <= 30) return 'info'
  return 'danger'
}

function statusTagType(status: string): string {
  const map: Record<string, string> = {
    success: 'success',
    pending: 'info',
    blocked: 'danger',
    timeout: 'warning',
    error: 'danger',
  }
  return map[status] || 'info'
}

// ============ 生命周期 ============
function handleResize() {
  trendChart?.resize()
}

onMounted(async () => {
  await loadSerpConfig()
  await reloadKeywords()
  await loadLatest()
  window.addEventListener('resize', handleResize)
})

onActivated(async () => {
  await loadSerpConfig()
  await reloadKeywords()
  await loadLatest()
  nextTick(() => trendChart?.resize())
})

watch(activeTab, (tab) => {
  if (tab === 'trends') {
    nextTick(() => trendChart?.resize())
  } else if (tab === 'latest') {
    loadLatest()
  } else if (tab === 'snapshots' && snapshots.value.length === 0) {
    loadSnapshots()
  }
})

onBeforeUnmount(() => {
  window.removeEventListener('resize', handleResize)
  trendChart?.dispose()
  trendChart = null
})
</script>
