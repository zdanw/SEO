<template>
  <div class="page-container">
    <div class="page-header page-header--actions">
      <el-button type="primary" @click="$router.push('/articles/new')">
        <el-icon><Plus /></el-icon>&nbsp;新建文章
      </el-button>
    </div>

    <el-card shadow="never" class="content-card">
      <div class="filter-bar">
        <el-select v-model="statusFilter" placeholder="状态筛选" clearable style="width: 160px" @change="loadArticles">
          <el-option label="草稿" value="draft" />
          <el-option label="AI 生成" value="ai_generated" />
          <el-option label="已审核" value="reviewed" />
          <el-option label="已发布" value="published" />
        </el-select>
        <el-button @click="loadArticles">刷新</el-button>
      </div>

      <el-table :data="articles" v-loading="loading" stripe style="width: 100%; margin-top: 12px">
        <el-table-column prop="id" label="ID" width="60" />
        <el-table-column prop="title" label="标题" min-width="250" show-overflow-tooltip>
          <template #default="{ row }">
            <el-link type="primary" @click="goEdit(row.id)">{{ row.title }}</el-link>
          </template>
        </el-table-column>
        <el-table-column prop="status" label="状态" width="120">
          <template #default="{ row }">
            <el-tag :type="statusTagType(row.status)">{{ statusLabel(row.status) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="SEO 分" width="100" align="center">
          <template #default="{ row }">
            <el-tag v-if="row.seo_score !== null && row.seo_score !== undefined"
              :type="row.seo_score >= 70 ? 'success' : row.seo_score >= 40 ? 'warning' : 'danger'">
              {{ Number(row.seo_score).toFixed(0) }}
            </el-tag>
            <span v-else style="color: #c0c4cc">-</span>
          </template>
        </el-table-column>
        <el-table-column label="AI 检测" width="100" align="center">
          <template #default="{ row }">
            <el-tag v-if="row.ai_detected_score !== null && row.ai_detected_score !== undefined"
              :type="row.ai_detected_score < 30 ? 'success' : row.ai_detected_score < 60 ? 'warning' : 'danger'"
              size="small">
              {{ Number(row.ai_detected_score).toFixed(0) }}
            </el-tag>
            <span v-else style="color: #c0c4cc">-</span>
          </template>
        </el-table-column>
        <el-table-column prop="updated_at" label="更新时间" width="170">
          <template #default="{ row }">{{ formatTime(row.updated_at) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="240" fixed="right">
          <template #default="{ row }">
            <el-button size="small" @click="goEdit(row.id)">编辑</el-button>
            <el-button size="small" type="success" @click="goSeoCheck(row.id)">SEO 检查</el-button>
            <el-popconfirm title="确认删除？" @confirm="handleDelete(row.id)">
              <template #reference>
                <el-button size="small" type="danger">删除</el-button>
              </template>
            </el-popconfirm>
          </template>
        </el-table-column>
      </el-table>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { Plus } from '@element-plus/icons-vue'
import { listArticles, deleteArticle, type Article } from '@/api/articles'

const router = useRouter()
const articles = ref<Article[]>([])
const loading = ref(false)
const statusFilter = ref('')

async function loadArticles() {
  loading.value = true
  try {
    articles.value = await listArticles(statusFilter.value ? { status: statusFilter.value } : undefined)
  } catch {
    // 错误已由 http 拦截器处理
  } finally {
    loading.value = false
  }
}

function goEdit(id: number) {
  router.push(`/articles/${id}`)
}

function goSeoCheck(id: number) {
  router.push({ path: '/seo-checker', query: { article_id: id } })
}

async function handleDelete(id: number) {
  await deleteArticle(id)
  ElMessage.success('已删除')
  loadArticles()
}

function statusLabel(s: string): string {
  return { draft: '草稿', ai_generated: 'AI 生成', reviewed: '已审核', published: '已发布' }[s] || s
}

function statusTagType(s: string): any {
  return { draft: 'info', ai_generated: 'warning', reviewed: '', published: 'success' }[s] || 'info'
}

function formatTime(t: string): string {
  return new Date(t).toLocaleString('zh-CN')
}

onMounted(loadArticles)
</script>
