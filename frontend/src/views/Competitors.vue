<template>
  <div class="page-container">
    <el-row :gutter="16">
      <el-col :xs="24" :lg="8">
        <el-card shadow="never" class="content-card">
          <template #header><span style="font-weight: 600">竞品列表</span></template>
          <div style="margin-bottom: 12px">
            <el-button type="primary" size="small" @click="openDialog">添加竞品</el-button>
            <el-button size="small" @click="loadCompetitors">刷新</el-button>
          </div>
          <el-table
            :data="competitors"
            v-loading="listLoading"
            stripe
            highlight-current-row
            @current-change="handleSelect"
          >
            <el-table-column prop="domain" label="域名" min-width="150" />
            <el-table-column prop="name" label="名称" width="100" />
            <el-table-column label="操作" width="80" fixed="right">
              <template #default="{ row }">
                <el-popconfirm title="确认删除？" @confirm="handleDelete(row.id)">
                  <template #reference><el-button size="small" type="danger" link>删除</el-button></template>
                </el-popconfirm>
              </template>
            </el-table-column>
          </el-table>
        </el-card>
      </el-col>

      <!-- 排名趋势 -->
      <el-col :xs="24" :lg="16">
        <el-card shadow="never" class="content-card">
          <template #header>
            <span style="font-weight: 600">
              {{ selectedCompetitor ? `${selectedCompetitor.domain} 排名趋势` : '选择竞品查看排名' }}
            </span>
          </template>
          <div v-if="selectedCompetitor" class="filter-bar" style="margin-bottom: 12px">
            <el-select v-model="rankDays" style="width: 120px" @change="loadRanks">
              <el-option :value="7" label="近 7 天" />
              <el-option :value="30" label="近 30 天" />
              <el-option :value="90" label="近 90 天" />
            </el-select>
            <el-button size="small" @click="loadRanks">刷新</el-button>
          </div>
          <el-alert
            v-if="rankHint"
            type="info"
            :closable="false"
            :title="rankHint"
            style="margin-bottom: 12px"
          />
          <div v-if="selectedCompetitor" ref="rankChartRef" style="height: 400px"></div>
          <div v-if="!selectedCompetitor" style="text-align: center; padding: 100px 0; color: #909399">
            请从左侧选择一个竞品域名
          </div>
        </el-card>
      </el-col>
    </el-row>

    <!-- 添加竞品对话框 -->
    <el-dialog v-model="dialogVisible" title="添加竞品" width="400px">
      <el-form :model="form" label-width="80px">
        <el-form-item label="域名" required>
          <el-input v-model="form.domain" placeholder="example.com" />
        </el-form-item>
        <el-form-item label="名称">
          <el-input v-model="form.name" placeholder="竞品名称（可选）" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="formLoading" @click="handleSubmit">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted, onBeforeUnmount, nextTick } from 'vue'
import * as echarts from 'echarts'
import { ElMessage } from 'element-plus'
import {
  listCompetitors,
  createCompetitor,
  deleteCompetitor,
  getCompetitorRanks,
  type Competitor,
  type CompetitorRank,
} from '@/api/competitors'
import { applyRankTrendChart } from '@/utils/rankTrendChart'

const competitors = ref<Competitor[]>([])
const listLoading = ref(false)
const selectedCompetitor = ref<Competitor | null>(null)

const rankChartRef = ref<HTMLElement>()
let rankChart: echarts.ECharts | null = null
const rankDays = ref(30)
const ranks = ref<CompetitorRank[]>([])

const dialogVisible = ref(false)
const formLoading = ref(false)
const form = ref({ domain: '', name: '' })
const rankHint = ref('')

async function loadCompetitors() {
  listLoading.value = true
  try {
    competitors.value = await listCompetitors()
  } finally {
    listLoading.value = false
  }
}

function openDialog() {
  form.value = { domain: '', name: '' }
  dialogVisible.value = true
}

async function handleSubmit() {
  if (!form.value.domain) {
    ElMessage.warning('域名不能为空')
    return
  }
  formLoading.value = true
  try {
    await createCompetitor({ domain: form.value.domain, name: form.value.name || undefined })
    ElMessage.success('竞品添加成功')
    dialogVisible.value = false
    await loadCompetitors()
  } finally {
    formLoading.value = false
  }
}

async function handleDelete(id: number) {
  await deleteCompetitor(id)
  ElMessage.success('已删除')
  if (selectedCompetitor.value?.id === id) {
    selectedCompetitor.value = null
    ranks.value = []
  }
  await loadCompetitors()
}

function handleSelect(row: Competitor | null) {
  selectedCompetitor.value = row
  if (row) {
    loadRanks()
  }
}

async function loadRanks() {
  if (!selectedCompetitor.value) return
  try {
    ranks.value = await getCompetitorRanks(selectedCompetitor.value.id, rankDays.value)
    renderRankChart()
  } catch {
    // handled
  }
}

function renderRankChart() {
  if (!selectedCompetitor.value || !rankChartRef.value) return
  if (!rankChart) {
    rankChart = echarts.init(rankChartRef.value)
  }

  const kwMap = new Map<string, CompetitorRank[]>()
  for (const r of ranks.value) {
    const key = r.keyword
    if (!kwMap.has(key)) kwMap.set(key, [])
    kwMap.get(key)!.push(r)
  }

  const seriesInput = Array.from(kwMap.entries()).map(([kw, points]) => ({
    name: kw,
    points: points.map((p) => ({ time: p.time, rank: p.rank ?? null })),
  }))

  const meta = applyRankTrendChart(rankChart, seriesInput, {
    periodDays: rankDays.value,
    gridTop: 40,
  })
  rankHint.value =
    meta.pointCount < 2
      ? '当前仅 1 个时间点，缩放条无效；请多次抓取排名后再查看趋势'
      : !meta.showDataZoom
        ? '时间跨度较短，已隐藏底部缩放条；可在图表内滚轮缩放'
        : ''
}

function handleResize() {
  rankChart?.resize()
}

onMounted(async () => {
  await loadCompetitors()
  window.addEventListener('resize', handleResize)
})

onBeforeUnmount(() => {
  window.removeEventListener('resize', handleResize)
  rankChart?.dispose()
  rankChart = null
})
</script>
