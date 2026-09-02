<template>
  <div class="page-container">
    <div class="page-toolbar">
      <el-select v-model="periodDays" placeholder="时间范围" style="width: 120px" @change="loadAll">
        <el-option label="近 7 天" :value="7" />
        <el-option label="近 14 天" :value="14" />
        <el-option label="近 30 天" :value="30" />
        <el-option label="近 90 天" :value="90" />
      </el-select>
      <el-button type="primary" :loading="generating" @click="onGenerate">重新生成建议</el-button>
      <el-button @click="onExportReport">导出月度报告</el-button>
      <el-button @click="loadAll" :loading="loading">刷新</el-button>
    </div>

    <!-- 6 张核心统计卡片 -->
    <el-row :gutter="16" class="stat-card-grid">
      <el-col v-for="card in cards" :key="card.label" :xs="12" :sm="12" :md="8" :xl="4" class="stat-col">
        <el-card shadow="hover" class="stat-card" :body-style="{ padding: '18px 20px' }">
          <div class="label">{{ card.label }}</div>
          <div class="value" :style="{ color: card.color }">{{ card.value }}</div>
          <div v-if="card.trend != null" class="trend" :class="card.trend >= 0 ? 'up' : 'down'">
            {{ card.trend >= 0 ? '↑' : '↓' }} {{ Math.abs(card.trend) }}% 较上周期
          </div>
        </el-card>
      </el-col>
    </el-row>

    <!-- 图表区 -->
    <el-row :gutter="16" style="margin-top: 4px">
      <el-col :span="24">
        <el-card shadow="hover" class="chart-card">
          <template #header>
            <span>关键词排名趋势</span>
            <el-tag v-if="!hasTrendData" type="info" size="small" style="margin-left:8px">暂无数据</el-tag>
            <el-tag v-else-if="rankTrendHint" type="warning" size="small" style="margin-left:8px">{{ rankTrendHint }}</el-tag>
          </template>
          <div ref="rankChartRef" style="height: 320px"></div>
        </el-card>
      </el-col>
    </el-row>

    <el-row :gutter="16">
      <el-col :xs="24" :md="12">
        <el-card shadow="hover" class="chart-card">
          <template #header>社交引流漏斗</template>
          <div ref="funnelChartRef" style="height: 280px"></div>
        </el-card>
      </el-col>
      <el-col :xs="24" :md="12">
        <el-card shadow="hover" class="chart-card">
          <template #header>外链统计</template>
          <div ref="backlinkChartRef" style="height: 280px"></div>
        </el-card>
      </el-col>
    </el-row>

    <!-- 待处理建议摘要 -->
    <el-card v-if="pendingCount > 0" shadow="hover" class="content-card" style="margin-top: 4px">
      <template #header>
        <span>待处理建议</span>
        <el-tag type="danger" style="margin-left:8px">{{ pendingCount }} 条待处理</el-tag>
      </template>
      <el-table :data="pendingRecs" stripe size="small" max-height="200">
        <el-table-column prop="severity" label="级别" width="80">
          <template #default="{ row }">
            <el-tag :type="severityType(row.severity)" size="small">{{ row.severity }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="title" label="标题" show-overflow-tooltip />
        <el-table-column prop="category" label="分类" width="90">
          <template #default="{ row }">
            <el-tag :type="categoryType(row.category)" size="small">{{ row.category }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="100">
          <template #default="{ row }">
            <el-button link type="primary" size="small" @click="onResolve(row)">处理</el-button>
            <el-button link type="danger" size="small" @click="onIgnore(row)">忽略</el-button>
          </template>
        </el-table-column>
      </el-table>
      <el-link type="primary" :underline="false" style="margin-top:8px;display:block" @click="$router.push('/recommendations')">
        查看全部建议 →
      </el-link>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { onMounted, onUnmounted, ref } from 'vue'
import * as echarts from 'echarts'
import { ElMessage } from 'element-plus'
import {
  getDashboardSummary,
  getRankTrends,
  getSocialFunnel,
  getBacklinkStats,
  generateRecommendations,
  listRecommendations,
  updateRecommendation,
  type DashboardCard,
  type TrendPoint,
  type Recommendation,
} from '@/api/dashboard'
import { exportMonthlyReport } from '@/api/reports'
import { listKeywords } from '@/api/articles'
import { applyRankTrendChart, formatKeywordTrendLabel } from '@/utils/rankTrendChart'

const periodDays = ref(7)
const loading = ref(false)
const generating = ref(false)

const cards = ref<DashboardCard[]>([])
const hasTrendData = ref(false)
const rankTrendHint = ref('')

// 图表 DOM
const rankChartRef = ref<HTMLDivElement>()
const funnelChartRef = ref<HTMLDivElement>()
const backlinkChartRef = ref<HTMLDivElement>()

// 图表实例
let rankChart: echarts.ECharts | null = null
let funnelChart: echarts.ECharts | null = null
let backlinkChart: echarts.ECharts | null = null

// 待处理建议
const pendingRecs = ref<Recommendation[]>([])
const pendingCount = ref(0)

// ============ 加载所有数据 ============
async function loadAll() {
  loading.value = true
  try {
    await Promise.all([loadSummary(), loadRankTrends(), loadSocialFunnel(), loadBacklinkStats(), loadPendingRecs()])
  } finally {
    loading.value = false
  }
}

async function loadSummary() {
  const data = await getDashboardSummary(periodDays.value)
  cards.value = data.cards
}

async function loadRankTrends() {
  const [data, keywords] = await Promise.all([
    getRankTrends(undefined, periodDays.value),
    listKeywords(),
  ])
  const kwById = new Map(keywords.map((k) => [k.id, k]))
  const keys = Object.keys(data)
  if (keys.length === 0) {
    hasTrendData.value = false
    rankTrendHint.value = ''
    rankChart?.setOption({ series: [] }, { notMerge: true })
    return
  }
  hasTrendData.value = keys.some((k) =>
    (data[k as unknown as number] || []).some((p: TrendPoint) => p.rank != null),
  )

  const seriesInput = keys.map((k) => {
    const kw = kwById.get(Number(k))
    return {
      name: kw ? formatKeywordTrendLabel(kw.keyword, kw.target_url) : `关键词 #${k}`,
      points: (data[k as unknown as number] || []) as TrendPoint[],
    }
  })

  const meta = applyRankTrendChart(rankChart, seriesInput, {
    periodDays: periodDays.value,
    yAxisName: '排名（越小越好）',
  })
  rankTrendHint.value =
    meta.pointCount >= 2 && !meta.showDataZoom
      ? '时间点过密，缩放条已隐藏'
      : meta.pointCount < 2
        ? '仅 1 次抓取，多次抓取后可缩放查看趋势'
        : ''
}

async function loadSocialFunnel() {
  const data = await getSocialFunnel(periodDays.value)
  const hasData = data.articles_published > 0 || data.posted_posts > 0 || data.total_clicks > 0
  if (!hasData) {
    funnelChart?.setOption({
      title: {
        text: '暂无数据',
        subtext: '发布文章并通过社交分发后显示漏斗',
        left: 'center',
        top: 'center',
        textStyle: { color: '#909399', fontSize: 14, fontWeight: 'normal' },
        subtextStyle: { color: '#C0C4CC', fontSize: 12 },
      },
      series: [],
    }, true)
    return
  }

  const funnelData = [
    { value: data.articles_published, name: '已发布文章' },
    { value: data.posted_posts, name: '成功发帖' },
    { value: data.total_clicks, name: '引流点击' },
  ]

  funnelChart?.setOption({
    tooltip: { trigger: 'item', formatter: '{b}: {c}' },
    series: [{
      type: 'funnel',
      left: '10%',
      top: 20,
      bottom: 20,
      width: '80%',
      min: 0,
      max: data.articles_published > 0 ? data.articles_published : 1,
      minSize: '0%',
      maxSize: '100%',
      sort: 'descending',
      gap: 2,
      label: { show: true, position: 'inside' },
      itemStyle: { borderColor: '#fff', borderWidth: 1 },
      emphasis: { label: { fontSize: 14 } },
      data: funnelData,
    }],
  }, true)
}

async function loadBacklinkStats() {
  const data = await getBacklinkStats(periodDays.value)
  if (data.total_backlinks === 0) {
    backlinkChart?.setOption({
      title: {
        text: '暂无外链',
        subtext: '在「外链监控」添加外链后显示统计',
        left: 'center',
        top: 'center',
        textStyle: { color: '#909399', fontSize: 14, fontWeight: 'normal' },
        subtextStyle: { color: '#C0C4CC', fontSize: 12 },
      },
      series: [],
    }, true)
    return
  }

  backlinkChart?.setOption({
    tooltip: { trigger: 'item' },
    legend: { bottom: 0 },
    series: [{
      type: 'pie',
      radius: ['45%', '75%'],
      avoidLabelOverlap: false,
      itemStyle: { borderRadius: 6, borderColor: '#fff', borderWidth: 2 },
      label: { show: true, formatter: '{b}\n{d}%' },
      data: [
        { value: data.alive_backlinks, name: '存活外链', itemStyle: { color: '#67C23A' } },
        { value: data.total_backlinks - data.alive_backlinks, name: '已失效', itemStyle: { color: '#F56C6C' } },
      ],
    }],
  }, true)
}

async function loadPendingRecs() {
  const res = await listRecommendations({ status: 'open', size: 5 })
  pendingRecs.value = res.items
  pendingCount.value = res.total
}

// ============ 操作 ============
async function onGenerate() {
  generating.value = true
  try {
    const res = await generateRecommendations(periodDays.value)
    ElMessage.success(`生成了 ${res.created} 条新建议`)
    await loadPendingRecs()
  } finally {
    generating.value = false
  }
}

async function onExportReport() {
  try {
    const md = await exportMonthlyReport(periodDays.value >= 28 ? periodDays.value : 30)
    const blob = new Blob([md], { type: 'text/markdown;charset=utf-8' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `seo-report-${periodDays.value}d.md`
    a.click()
    URL.revokeObjectURL(url)
    ElMessage.success('报告已导出')
  } catch {
    ElMessage.error('导出失败')
  }
}

async function onResolve(rec: Recommendation) {
  await updateRecommendation(rec.id, { status: 'in_progress' })
  await loadPendingRecs()
}

async function onIgnore(rec: Recommendation) {
  await updateRecommendation(rec.id, { status: 'ignored' })
  await loadPendingRecs()
}

function severityType(sev: string) {
  return { critical: 'danger', warning: 'warning', info: 'info' }[sev] ?? 'info'
}

function categoryType(cat: string) {
  return { content: '', performance: 'danger', social: 'warning', link: 'success', crawler: 'info' }[cat] ?? ''
}

// ============ 生命周期 ============
function handleResize() {
  rankChart?.resize()
  funnelChart?.resize()
  backlinkChart?.resize()
}

onMounted(() => {
  rankChart = echarts.init(rankChartRef.value!)
  funnelChart = echarts.init(funnelChartRef.value!)
  backlinkChart = echarts.init(backlinkChartRef.value!)
  window.addEventListener('resize', handleResize)
  loadAll()
})

onUnmounted(() => {
  window.removeEventListener('resize', handleResize)
  rankChart?.dispose()
  funnelChart?.dispose()
  backlinkChart?.dispose()
})
</script>
