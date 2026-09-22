<template>
  <div class="page-container">
    <div class="page-toolbar">
      <el-button type="primary" :loading="loading" @click="refresh">刷新</el-button>
    </div>

    <el-alert
      v-for="(msg, i) in queueAlerts"
      :key="i"
      :title="msg"
      type="warning"
      show-icon
      :closable="false"
      style="margin-bottom: 12px"
    />

    <el-row :gutter="16" class="stat-card-grid">
      <el-col v-for="q in queueCards" :key="q.name" :xs="12" :sm="8" :md="8">
        <el-card shadow="hover" class="stat-card" :body-style="{ padding: '18px 20px' }" v-loading="loading">
          <div class="label">队列 {{ q.name }}</div>
          <div class="value" :style="{ color: q.alert ? '#F56C6C' : '#409EFF' }">
            {{ q.depth == null ? '—' : q.depth }}
          </div>
          <div class="muted">积压深度（阈值 {{ alertThreshold }}）</div>
        </el-card>
      </el-col>
    </el-row>

    <el-row :gutter="16" style="margin-top: 8px">
      <el-col :xs="24" :md="12">
        <el-card shadow="never" class="section-card" v-loading="loading">
          <template #header>
            <span class="card-title">今日 SERP 成本 / 配额</span>
          </template>
          <template v-if="costs">
            <p class="muted">Provider：{{ costs.serp_provider }} · 代理降级：{{ costs.serp_allow_proxy_fallback ? '开' : '关' }}</p>
            <el-descriptions :column="1" size="small" border>
              <el-descriptions-item label="已用">{{ costs.serp_today.used }}</el-descriptions-item>
              <el-descriptions-item label="剩余">
                {{ costs.serp_today.remaining == null ? '不限' : costs.serp_today.remaining }}
                / 日配额 {{ costs.serp_today.daily_quota <= 0 ? '不限' : costs.serp_today.daily_quota }}
              </el-descriptions-item>
              <el-descriptions-item label="估算费用 (USD)">
                {{ costs.serp_today.estimated_cost_usd }}
                （≈ {{ costs.serp_today.est_cost_per_request }}/次）
              </el-descriptions-item>
            </el-descriptions>
          </template>
          <el-empty v-else description="暂无成本数据" />
        </el-card>
      </el-col>
      <el-col :xs="24" :md="12">
        <el-card shadow="never" class="section-card" v-loading="loading">
          <template #header>
            <span class="card-title">近 7 日发布成功率</span>
          </template>
          <template v-if="costs">
            <el-descriptions :column="1" size="small" border>
              <el-descriptions-item label="帖子">
                {{ formatRate(costs.publish_7d.posts_success_rate) }}
                （{{ costs.publish_7d.posts_ok }}/{{ costs.publish_7d.posts_total }}）
              </el-descriptions-item>
              <el-descriptions-item label="评论">
                {{ formatRate(costs.publish_7d.comments_success_rate) }}
                （{{ costs.publish_7d.comments_ok }}/{{ costs.publish_7d.comments_total }}）
              </el-descriptions-item>
            </el-descriptions>
            <p class="sample-note">{{ costs.note }}</p>
          </template>
          <el-empty v-else description="暂无发布数据" />
        </el-card>
      </el-col>
    </el-row>

    <el-card shadow="never" class="section-card" style="margin-top: 16px">
      <template #header>
        <div class="card-title-row">
          <span class="card-title">任务台账</span>
          <div class="filter-row">
            <el-select v-model="filterType" clearable placeholder="业务类型" style="width: 160px" @change="loadTasks">
              <el-option label="reddit_post" value="reddit_post" />
              <el-option label="reddit_comment" value="reddit_comment" />
              <el-option label="serp_keyword" value="serp_keyword" />
            </el-select>
            <el-input
              v-model="filterBizId"
              clearable
              placeholder="业务 ID"
              style="width: 120px"
              @keyup.enter="loadTasks"
            />
            <el-button @click="loadTasks" :loading="tasksLoading">查询</el-button>
          </div>
        </div>
      </template>
      <el-table :data="tasks" v-loading="tasksLoading" size="small" stripe empty-text="暂无任务记录">
        <el-table-column prop="task_name" label="任务" min-width="180" show-overflow-tooltip />
        <el-table-column label="业务" width="140">
          <template #default="{ row }">
            <span v-if="row.business_type">{{ row.business_type }}#{{ row.business_id }}</span>
            <span v-else class="muted">—</span>
          </template>
        </el-table-column>
        <el-table-column prop="status" label="状态" width="100">
          <template #default="{ row }">
            <el-tag :type="statusTag(row.status)" size="small">{{ row.status }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="started_at" label="开始" width="170" />
        <el-table-column prop="finished_at" label="结束" width="170" />
        <el-table-column prop="error_message" label="错误" min-width="160" show-overflow-tooltip />
        <el-table-column prop="request_id" label="Request ID" width="120" show-overflow-tooltip />
      </el-table>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import {
  getCostSnapshot,
  getQueueHealth,
  listRecentTasks,
  type CostSnapshot,
  type TaskRunRow,
} from '@/api/ops'

const loading = ref(false)
const tasksLoading = ref(false)
const costs = ref<CostSnapshot | null>(null)
const tasks = ref<TaskRunRow[]>([])
const queueMap = ref<Record<string, { depth: number | null; alert: boolean }>>({})
const queueAlerts = ref<string[]>([])
const alertThreshold = ref(100)
const filterType = ref<string | undefined>()
const filterBizId = ref('')

const queueCards = computed(() =>
  ['serp', 'social', 'default'].map((name) => ({
    name,
    depth: queueMap.value[name]?.depth ?? null,
    alert: queueMap.value[name]?.alert ?? false,
  })),
)

function formatRate(v: number | null) {
  return v == null ? '—' : `${v}%`
}

function statusTag(s: string) {
  if (s === 'success') return 'success'
  if (s === 'failed') return 'danger'
  if (s === 'running') return 'warning'
  return 'info'
}

async function loadQueuesAndCosts() {
  loading.value = true
  try {
    const [q, c] = await Promise.all([getQueueHealth(), getCostSnapshot()])
    queueMap.value = q.queues
    queueAlerts.value = q.alerts || []
    alertThreshold.value = q.alert_threshold
    costs.value = c
  } finally {
    loading.value = false
  }
}

async function loadTasks() {
  tasksLoading.value = true
  try {
    const bizId = filterBizId.value.trim()
    tasks.value = await listRecentTasks({
      business_type: filterType.value || undefined,
      business_id: bizId ? Number(bizId) : undefined,
      limit: 50,
    })
  } finally {
    tasksLoading.value = false
  }
}

async function refresh() {
  await Promise.all([loadQueuesAndCosts(), loadTasks()])
}

onMounted(refresh)
</script>

<style scoped lang="scss">
.page-toolbar {
  display: flex;
  justify-content: flex-end;
  margin-bottom: 12px;
}
.card-title-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
}
.filter-row {
  display: flex;
  gap: 8px;
  align-items: center;
  flex-wrap: wrap;
}
.muted {
  color: var(--el-text-color-secondary);
  font-size: 12px;
}
.sample-note {
  margin-top: 12px;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}
.section-card {
  margin-bottom: 8px;
}
</style>
