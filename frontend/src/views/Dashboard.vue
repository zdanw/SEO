<template>
  <div class="page-container">
    <div class="page-toolbar">
      <el-select v-model="periodDays" placeholder="时间范围" style="width: 120px" @change="loadAll">
        <el-option label="近 7 天" :value="7" />
        <el-option label="近 14 天" :value="14" />
        <el-option label="近 30 天" :value="30" />
        <el-option label="近 90 天" :value="90" />
      </el-select>
      <el-button @click="loadAll" :loading="loading">刷新</el-button>
    </div>

    <el-row :gutter="16" class="stat-card-grid">
      <el-col v-for="card in cards" :key="card.label" :xs="12" :sm="12" :md="8" :xl="6" class="stat-col">
        <el-card shadow="hover" class="stat-card" :body-style="{ padding: '18px 20px' }">
          <div class="label">{{ card.label }}</div>
          <div class="value" :style="{ color: card.color }">{{ card.value }}</div>
          <div v-if="card.trend != null" class="trend" :class="card.trend >= 0 ? 'up' : 'down'">
            {{ card.trend >= 0 ? '↑' : '↓' }} {{ Math.abs(card.trend) }}% 较上周期
          </div>
        </el-card>
      </el-col>
    </el-row>

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
      <el-col :span="24">
        <el-card shadow="hover" class="chart-card">
          <template #header>社交分发漏斗</template>
          <div ref="funnelChartRef" style="height: 280px"></div>
        </el-card>
      </el-col>
    </el-row>
  </div>
</template>

<script setup lang="ts">
import { onMounted, onUnmounted, ref } from 'vue'
import * as echarts from 'echarts'
import {
  getDashboardSummary,
  getRankTrends,
  getSocialFunnel,
  type DashboardCard,
  type TrendPoint,
} from '@/api/dashboard'
import { listKeywords } from '@/api/keywords'
import { applyRankTrendChart, formatKeywordTrendLabel } from '@/utils/rankTrendChart'

const periodDays = ref(7)
const loading = ref(false)

const cards = ref<DashboardCard[]>([])
const hasTrendData = ref(false)
const rankTrendHint = ref('')

const rankChartRef = ref<HTMLDivElement>()
const funnelChartRef = ref<HTMLDivElement>()

let rankChart: echarts.ECharts | null = null
let funnelChart: echarts.ECharts | null = null

async function loadAll() {
  loading.value = true
  try {
    await Promise.all([loadSummary(), loadRankTrends(), loadSocialFunnel()])
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
  const hasData = data.total_posts > 0 || data.posted_posts > 0 || data.total_clicks > 0
  if (!hasData) {
    funnelChart?.setOption({
      title: {
        text: '暂无数据',
        subtext: '在「社交分发」创建发帖任务后显示漏斗',
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
    { value: data.total_posts, name: '创建任务' },
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
      max: data.total_posts > 0 ? data.total_posts : 1,
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

function handleResize() {
  rankChart?.resize()
  funnelChart?.resize()
}

onMounted(() => {
  rankChart = echarts.init(rankChartRef.value!)
  funnelChart = echarts.init(funnelChartRef.value!)
  window.addEventListener('resize', handleResize)
  loadAll()
})

onUnmounted(() => {
  window.removeEventListener('resize', handleResize)
  rankChart?.dispose()
  funnelChart?.dispose()
})
</script>
