<template>
  <div class="page-container">
    <div class="page-toolbar">
      <el-select v-model="filterSeverity" placeholder="严重程度" clearable style="width:120px" @change="load">
        <el-option label="严重" value="critical" />
        <el-option label="警告" value="warning" />
        <el-option label="提示" value="info" />
      </el-select>
      <el-select v-model="filterCategory" placeholder="分类" clearable style="width:120px" @change="load">
        <el-option label="内容" value="content" />
        <el-option label="性能" value="performance" />
        <el-option label="社交" value="social" />
        <el-option label="外链" value="link" />
        <el-option label="爬虫" value="crawler" />
      </el-select>
      <el-select v-model="filterStatus" placeholder="状态" clearable style="width:120px" @change="load">
        <el-option label="待处理" value="open" />
        <el-option label="处理中" value="in_progress" />
        <el-option label="已解决" value="resolved" />
        <el-option label="已忽略" value="ignored" />
      </el-select>
      <el-button type="primary" :loading="generating" @click="onGenerate">重新生成建议</el-button>
      <el-button @click="load">刷新</el-button>
      <span class="count">共 {{ total }} 条</span>
    </div>

    <!-- 工单列表（Trello 风格卡片） -->
    <el-empty v-if="!loading && items.length === 0" description="暂无建议工单，点击「重新生成建议」触发策略引擎" />

    <div v-else class="cards">
      <el-card
        v-for="rec in items"
        :key="rec.id"
        shadow="hover"
        class="rec-card"
        :class="{ 'rec-card--resolved': rec.status === 'resolved', 'rec-card--ignored': rec.status === 'ignored' }"
      >
        <div class="rec-header">
          <el-tag :type="severityType(rec.severity)" size="small">{{ severityLabel(rec.severity) }}</el-tag>
          <el-tag :type="categoryTagType(rec.category)" size="small" style="margin-left:6px">{{ categoryLabel(rec.category) }}</el-tag>
          <el-tag v-if="rec.status !== 'open'" :type="statusTagType(rec.status)" size="small" style="margin-left:auto">{{ statusLabel(rec.status) }}</el-tag>
        </div>
        <div class="rec-title">{{ rec.title }}</div>
        <div v-if="rec.description" class="rec-desc">{{ rec.description }}</div>
        <div v-if="rec.suggestion" class="rec-suggestion">
          <span class="suggestion-label">建议：</span>{{ rec.suggestion }}
        </div>
        <div class="rec-footer">
          <span class="rec-time">{{ formatDate(rec.created_at) }}</span>
          <div class="rec-actions">
            <el-button v-if="rec.status === 'open'" link type="primary" size="small" @click="onAction(rec, 'in_progress')">处理中</el-button>
            <el-button v-if="rec.status === 'in_progress'" link type="success" size="small" @click="onAction(rec, 'resolved')">标记解决</el-button>
            <el-button link type="danger" size="small" @click="onAction(rec, 'ignored')">忽略</el-button>
            <el-button link type="danger" size="small" @click="onDelete(rec.id)">删除</el-button>
          </div>
        </div>
      </el-card>
    </div>

    <!-- 分页 -->
    <el-pagination
      v-if="total > pageSize"
      style="margin-top:16px;justify-content:center"
      layout="prev, pager, next"
      :total="total"
      :page-size="pageSize"
      v-model:current-page="currentPage"
      @current-change="load"
    />
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  listRecommendations,
  generateRecommendations,
  updateRecommendation,
  deleteRecommendation,
  type Recommendation,
} from '@/api/dashboard'

const items = ref<Recommendation[]>([])
const total = ref(0)
const currentPage = ref(1)
const pageSize = 20
const loading = ref(false)
const generating = ref(false)

const filterSeverity = ref<string | undefined>()
const filterCategory = ref<string | undefined>()
const filterStatus = ref<string | undefined>()

async function load() {
  loading.value = true
  try {
    const params: Record<string, any> = { page: currentPage.value, size: pageSize }
    if (filterSeverity.value) params.severity = filterSeverity.value
    if (filterCategory.value) params.category = filterCategory.value
    if (filterStatus.value) params.status = filterStatus.value
    const res = await listRecommendations(params)
    items.value = res.items
    total.value = res.total
  } finally {
    loading.value = false
  }
}

async function onGenerate() {
  generating.value = true
  try {
    const res = await generateRecommendations()
    ElMessage.success(`生成了 ${res.created} 条新建议`)
    await load()
  } finally {
    generating.value = false
  }
}

async function onAction(rec: Recommendation, status: string) {
  await updateRecommendation(rec.id, { status })
  ElMessage.success(`已标记为 ${statusLabel(status)}`)
  await load()
}

async function onDelete(id: number) {
  await ElMessageBox.confirm('确定要删除该建议工单？', '提示', { type: 'warning' })
  await deleteRecommendation(id)
  ElMessage.success('已删除')
  await load()
}

function severityType(sev: string) {
  return { critical: 'danger', warning: 'warning', info: 'info' }[sev] ?? 'info'
}
function severityLabel(sev: string) {
  return { critical: '严重', warning: '警告', info: '提示' }[sev] ?? sev
}
function categoryLabel(cat: string) {
  return { content: '内容', performance: '性能', social: '社交', link: '外链', crawler: '爬虫' }[cat] ?? cat
}
function categoryTagType(cat: string) {
  return { content: '', performance: 'danger', social: 'warning', link: 'success', crawler: 'info' }[cat] ?? ''
}
function statusLabel(s: string) {
  return { open: '待处理', in_progress: '处理中', resolved: '已解决', ignored: '已忽略' }[s] ?? s
}
function statusTagType(s: string) {
  return { open: 'danger', in_progress: 'warning', resolved: 'success', ignored: 'info' }[s] ?? 'info'
}
function formatDate(dateStr: string | null) {
  if (!dateStr) return ''
  return new Date(dateStr).toLocaleDateString('zh-CN')
}

onMounted(load)
</script>

<style lang="scss" scoped>
.count {
  margin-left: auto;
  color: #909399;
  font-size: 13px;
}
.cards {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(340px, 1fr));
  gap: 14px;
}
.rec-card {
  &--resolved { opacity: 0.6; }
  &--ignored { opacity: 0.5; }
  .rec-header {
    display: flex;
    align-items: center;
    margin-bottom: 8px;
  }
  .rec-title {
    font-size: 14px;
    font-weight: 600;
    color: #303133;
    margin-bottom: 6px;
    line-height: 1.4;
  }
  .rec-desc {
    font-size: 13px;
    color: #606266;
    margin-bottom: 6px;
    line-height: 1.5;
  }
  .rec-suggestion {
    font-size: 13px;
    color: #409EFF;
    background: #ecf5ff;
    border-radius: 4px;
    padding: 6px 10px;
    margin-bottom: 10px;
    line-height: 1.5;
    .suggestion-label { font-weight: 600; }
  }
  .rec-footer {
    display: flex;
    justify-content: space-between;
    align-items: center;
    font-size: 12px;
    color: #909399;
    margin-top: 8px;
    padding-top: 8px;
    border-top: 1px solid #f0f0f0;
  }
  .rec-actions {
    display: flex;
    gap: 4px;
  }
}
</style>
