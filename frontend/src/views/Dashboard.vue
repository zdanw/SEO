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
          <p class="sample-note">{{ sampleNote }}</p>
          <div ref="rankChartRef" style="height: 320px"></div>
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
  type DashboardCard,
  type TrendPoint,
} from '@/api/dashboard'
import { listKeywords } from '@/api/keywords'
import { applyRankTrendChart, formatKeywordTrendLabel } from '@/utils/rankTrendChart'
import { useSiteReload } from '@/composables/useSiteReload'

const periodDays = ref(7)
const loading = ref(false)

const cards = ref<DashboardCard[]>([])
const hasTrendData = ref(false)
const rankTrendHint = ref('')
const sampleNote = ref('指标仅统计成功抓取样本；排名与社交互动不做自动因果归因。')

const rankChartRef = ref<HTMLDivElement>()

let rankChart: echarts.ECharts | null = null

async function loadAll() {
  loading.value = true
  try {
    await Promise.all([loadSummary(), loadRankTrends()])
  } finally {
    loading.value = false
  }
}

async function loadSummary() {
  const data = await getDashboardSummary(periodDays.value)
  cards.value = data.cards
  if (data.sample) {
    sampleNote.value = `${data.sample.time_range} · ${data.sample.rank_scope}。${data.sample.disclaimer}`
  }
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

function handleResize() {
  rankChart?.resize()
}

onMounted(() => {
  rankChart = echarts.init(rankChartRef.value!)
  window.addEventListener('resize', handleResize)
  loadAll()
})

useSiteReload(loadAll)

onUnmounted(() => {
  window.removeEventListener('resize', handleResize)
  rankChart?.dispose()
})
</script>

<style scoped>
.sample-note {
  margin: 0 0 12px;
  font-size: 12px;
  color: var(--el-text-color-secondary);
  line-height: 1.5;
}
</style>
