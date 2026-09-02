<template>
  <div class="editor-page">
    <!-- 顶部工具栏 -->
    <div class="toolbar">
      <div class="toolbar-left">
        <el-button @click="$router.push('/articles')">返回列表</el-button>
        <el-input v-model="form.title" placeholder="文章标题..." style="width: 400px; margin-left: 12px" />
      </div>
      <div class="toolbar-right">
        <el-tag :type="statusTagType(form.status)">{{ statusLabel(form.status) }}</el-tag>
        <el-button type="primary" :loading="saving" @click="handleSave">保存</el-button>
        <el-button type="success" @click="statusDialog = true">切换状态</el-button>
        <el-button
          v-if="articleId"
          type="warning"
          :loading="syncLoading"
          @click="handleSyncCms"
        >
          同步到客户站
        </el-button>
      </div>
    </div>

    <div class="editor-body">
      <!-- 左栏：AI 写作 + Meta -->
      <div class="left-panel">
        <el-card shadow="never" class="ai-card">
          <template #header>
            <span style="font-weight: 600">AI 写作</span>
            <el-select v-model="aiForm.model" size="small" style="margin-left:8px;width:130px">
              <el-option label="DeepSeek（默认）" value="deepseek" />
              <el-option label="Agnes 2.5 Flash" value="agnes" />
            </el-select>
          </template>
          <el-form label-position="top" size="small">
            <el-form-item label="核心关键词">
              <el-input v-model="aiForm.keyword" placeholder="例如：Python asyncio 最佳实践" />
            </el-form-item>
            <el-form-item label="大纲（可选）">
              <el-input v-model="aiForm.outline" type="textarea" :rows="3" placeholder="输入文章大纲要点..." />
            </el-form-item>
            <el-form-item label="目标受众">
              <el-input v-model="aiForm.audience" />
            </el-form-item>
            <el-form-item label="语气风格">
              <el-input v-model="aiForm.tone" />
            </el-form-item>
            <el-button type="primary" :loading="aiLoading" style="width: 100%" @click="handleAIGenerate">
              AI 生成文章
            </el-button>
            <el-button
              v-if="aiForm.keyword && form.content"
              :loading="serpLoading"
              style="width: 100%; margin-top: 8px"
              @click="handleSerpScore"
            >
              SERP 内容评分
            </el-button>
          </el-form>
        </el-card>

        <el-card shadow="never" class="meta-card" style="margin-top: 12px">
          <template #header><span style="font-weight: 600">SEO 元信息</span></template>
          <el-form label-position="top" size="small">
            <el-form-item label="Meta Description">
              <el-input v-model="form.meta_description" type="textarea" :rows="3" maxlength="320" show-word-limit />
              <div style="margin-top: 4px">
                <el-button size="small" :loading="metaLoading" @click="handleAIMeta">AI 生成 Meta</el-button>
              </div>
            </el-form-item>
            <el-form-item label="Slug（URL 别名）">
              <el-input v-model="form.slug" />
            </el-form-item>
            <el-form-item label="封面图 URL">
              <el-input v-model="form.cover_image_url" />
            </el-form-item>
            <el-form-item label="目标 URL">
              <el-input v-model="form.target_url" />
            </el-form-item>
          </el-form>
        </el-card>
      </div>

      <!-- 中栏：Markdown 编辑器 -->
      <div class="center-panel">
        <MdEditor
          v-model="form.content"
          :style="{ height: 'calc(100vh - 140px)' }"
          language="zh-CN"
          :toolbars-exclude="['github', 'save', 'pageFullscreen', 'catalog']"
          placeholder="在此输入 Markdown 正文，或使用左侧 AI 写作面板自动生成..."
          @on-save="handleSave"
        />
      </div>

      <!-- 右栏：SEO 检查 -->
      <div class="right-panel">
        <el-card shadow="never" class="seo-card">
          <template #header>
            <div style="display: flex; justify-content: space-between; align-items: center">
              <span style="font-weight: 600">SEO 检查</span>
              <el-button size="small" type="primary" :loading="seoLoading" @click="handleSeoCheck">检查</el-button>
            </div>
          </template>

          <div v-if="seoResult" class="seo-result">
            <div class="score-bar">
              <div class="score-num" :style="{ color: scoreColor(seoResult.total_score) }">
                {{ seoResult.total_score.toFixed(0) }}
              </div>
              <div class="score-label">/ 100</div>
            </div>

            <el-divider style="margin: 12px 0" />

            <div v-for="item in seoResult.items" :key="item.key" class="check-item">
              <div class="check-header">
                <el-icon :color="statusColor(item.status)">
                  <CircleCheck v-if="item.status === 'pass'" />
                  <WarningFilled v-else-if="item.status === 'warning'" />
                  <CircleCloseFilled v-else />
                </el-icon>
                <span class="check-name">{{ item.name }}</span>
                <span class="check-score">{{ item.score }}/{{ item.max_score }}</span>
              </div>
              <div class="check-msg">{{ item.message }}</div>
              <div v-if="item.suggestion" class="check-sugg">→ {{ item.suggestion }}</div>
            </div>

            <el-divider style="margin: 12px 0" />

            <AiDetectDetail
              :score="seoResult.ai_detected_score"
              :detail="seoResult.ai_detail"
            />

            <SerpScoreDetail :detail="seoResult.serp_detail" />

            <div class="cwv-section">
              <div style="font-weight: 600; margin-bottom: 8px">
                Core Web Vitals
                <el-tag v-if="seoResult.cwv_estimate.source === 'estimated'" type="info" size="small" style="margin-left: 6px">静态预估</el-tag>
              </div>
              <div class="cwv-grid">
                <div class="cwv-item">
                  <div class="cwv-label">LCP</div>
                  <div :class="['cwv-val', riskClass(seoResult.cwv_estimate.lcp_risk)]">{{ seoResult.cwv_estimate.lcp_ms }}ms</div>
                </div>
                <div class="cwv-item">
                  <div class="cwv-label">CLS</div>
                  <div :class="['cwv-val', riskClass(seoResult.cwv_estimate.cls_risk)]">{{ seoResult.cwv_estimate.cls }}</div>
                </div>
                <div class="cwv-item">
                  <div class="cwv-label">INP</div>
                  <div :class="['cwv-val', riskClass(seoResult.cwv_estimate.inp_risk)]">{{ seoResult.cwv_estimate.inp_ms }}ms</div>
                </div>
              </div>
            </div>
          </div>
          <el-empty v-else description="点击「检查」按钮开始 SEO 分析" :image-size="60" />
        </el-card>
      </div>
    </div>

    <!-- 状态切换弹窗 -->
    <el-dialog v-model="statusDialog" title="切换文章状态" width="400px">
      <el-radio-group v-model="newStatus" style="display: flex; flex-direction: column; gap: 12px">
        <el-radio value="draft">草稿 Draft</el-radio>
        <el-radio value="ai_generated">AI 生成</el-radio>
        <el-radio value="reviewed">已审核</el-radio>
        <el-radio value="published">已发布</el-radio>
      </el-radio-group>
      <template #footer>
        <el-button @click="statusDialog = false">取消</el-button>
        <el-button type="primary" :loading="statusLoading" @click="handleStatusChange">确认</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { CircleCheck, WarningFilled, CircleCloseFilled } from '@element-plus/icons-vue'
import { MdEditor } from 'md-editor-v3'
import 'md-editor-v3/lib/style.css'
import {
  getArticle, createArticle, updateArticle, changeArticleStatus, syncArticleToCms,
  type Article,
} from '@/api/articles'
import { generateArticle, regenerateMeta, type AIArticleRequest } from '@/api/ai'
import { checkSeo, checkSeoAndSave, scoreSerpContent, type SeoCheckResult } from '@/api/seo'
import AiDetectDetail from '@/components/AiDetectDetail.vue'
import SerpScoreDetail from '@/components/SerpScoreDetail.vue'

const route = useRoute()
const router = useRouter()

const articleId = ref<number | null>(null)
const saving = ref(false)
const aiLoading = ref(false)
const metaLoading = ref(false)
const seoLoading = ref(false)
const serpLoading = ref(false)
const statusLoading = ref(false)
const syncLoading = ref(false)
const statusDialog = ref(false)
const newStatus = ref('')

const form = reactive({
  title: '',
  meta_description: '',
  slug: '',
  content: '',
  cover_image_url: '',
  target_url: '',
  status: 'draft' as Article['status'],
})

const aiForm = reactive<AIArticleRequest>({
  keyword: '',
  outline: '',
  audience: '技术从业者和内容创作者',
  tone: '专业、务实、易懂',
  model: 'deepseek',
})

const seoResult = ref<SeoCheckResult | null>(null)

onMounted(async () => {
  const id = route.params.id
  if (id && id !== 'new') {
    articleId.value = Number(id)
    await loadArticle()
  }
})

async function loadArticle() {
  if (!articleId.value) return
  const art = await getArticle(articleId.value)
  form.title = art.title
  form.meta_description = art.meta_description || ''
  form.slug = art.slug || ''
  form.content = art.content || ''
  form.cover_image_url = art.cover_image_url || ''
  form.target_url = art.target_url || ''
  form.status = art.status
  newStatus.value = art.status
}

async function handleSave() {
  if (!form.title.trim()) {
    ElMessage.warning('请填写标题')
    return
  }
  saving.value = true
  try {
    const payload = {
      title: form.title,
      meta_description: form.meta_description || undefined,
      slug: form.slug || undefined,
      content: form.content,
      cover_image_url: form.cover_image_url || undefined,
      target_url: form.target_url || undefined,
    }
    if (articleId.value) {
      await updateArticle(articleId.value, payload)
      ElMessage.success('已保存')
    } else {
      const created = await createArticle(payload)
      articleId.value = created.id
      form.status = created.status
      ElMessage.success('已创建')
      router.replace(`/articles/${created.id}`)
    }
  } finally {
    saving.value = false
  }
}

async function handleAIGenerate() {
  if (!aiForm.keyword.trim()) {
    ElMessage.warning('请输入核心关键词')
    return
  }
  aiLoading.value = true
  try {
    const draft = await generateArticle(aiForm)
    form.title = draft.title
    form.meta_description = draft.meta_description
    form.content = draft.content
    ElMessage.success('AI 文章已生成，请审阅后保存')
  } finally {
    aiLoading.value = false
  }
}

async function handleAIMeta() {
  if (!form.content || form.content.length < 50) {
    ElMessage.warning('正文内容过少，无法生成 Meta')
    return
  }
  if (!aiForm.keyword) {
    ElMessage.warning('请先在 AI 写作面板填写关键词')
    return
  }
  metaLoading.value = true
  try {
    const res = await regenerateMeta(aiForm.keyword, form.content)
    form.meta_description = res.meta_description
    ElMessage.success('Meta Description 已生成')
  } finally {
    metaLoading.value = false
  }
}

async function handleSeoCheck() {
  if (!form.title) {
    ElMessage.warning('请先填写标题')
    return
  }
  seoLoading.value = true
  try {
    if (articleId.value) {
      seoResult.value = await checkSeoAndSave(articleId.value, aiForm.keyword || undefined)
    } else {
      seoResult.value = await checkSeo({
        title: form.title,
        content: form.content,
        meta_description: form.meta_description,
        keyword: aiForm.keyword,
        cover_image_url: form.cover_image_url,
      })
    }
  } finally {
    seoLoading.value = false
  }
}

async function handleSerpScore() {
  if (!aiForm.keyword?.trim()) {
    ElMessage.warning('请先填写核心关键词')
    return
  }
  if (!form.content || form.content.length < 50) {
    ElMessage.warning('正文内容过少')
    return
  }
  serpLoading.value = true
  try {
    const serp = await scoreSerpContent(form.content, aiForm.keyword)
    if (!seoResult.value) {
      seoResult.value = {
        total_score: 0,
        items: [],
        ai_detected_score: 0,
        cwv_estimate: { lcp_ms: 0, lcp_risk: 'low', cls: 0, cls_risk: 'low', inp_ms: 0, inp_risk: 'low', image_count: 0, cover_image: false, note: '' },
      }
    }
    seoResult.value.serp_detail = serp
    ElMessage.success(`SERP 评分：${serp.overall_score}/100`)
  } finally {
    serpLoading.value = false
  }
}

async function handleSyncCms() {
  if (!articleId.value) return
  syncLoading.value = true
  try {
    const updated = await syncArticleToCms(articleId.value)
    form.target_url = updated.target_url || form.target_url
    ElMessage.success('已同步到客户 CMS')
  } finally {
    syncLoading.value = false
  }
}

async function handleStatusChange() {
  if (!articleId.value) {
    ElMessage.warning('请先保存文章')
    statusDialog.value = false
    return
  }
  statusLoading.value = true
  try {
    const updated = await changeArticleStatus(articleId.value, newStatus.value)
    form.status = updated.status
    statusDialog.value = false
    ElMessage.success(`状态已切换为：${statusLabel(updated.status)}`)
  } catch {
    // 拦截器已处理
  } finally {
    statusLoading.value = false
  }
}

function statusLabel(s: string): string {
  return { draft: '草稿', ai_generated: 'AI 生成', reviewed: '已审核', published: '已发布' }[s] || s
}
function statusTagType(s: string): any {
  return { draft: 'info', ai_generated: 'warning', reviewed: '', published: 'success' }[s] || 'info'
}
function statusColor(s: string): string {
  return { pass: '#67c23a', warning: '#e6a23c', fail: '#f56c6c' }[s] || '#909399'
}
function scoreColor(score: number): string {
  return score >= 70 ? '#67c23a' : score >= 40 ? '#e6a23c' : '#f56c6c'
}
function riskClass(risk: string): string {
  return { low: 'cwv-low', medium: 'cwv-medium', high: 'cwv-high' }[risk] || ''
}
</script>

<style lang="scss" scoped>
.editor-page {
  height: calc(100vh - 60px);
  display: flex;
  flex-direction: column;
}
.toolbar {
  height: 50px;
  background: #fff;
  border-bottom: 1px solid #e4e7ed;
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 0 16px;
  .toolbar-left, .toolbar-right {
    display: flex;
    align-items: center;
    gap: 8px;
  }
}
.editor-body {
  flex: 1;
  display: flex;
  overflow: hidden;
}
.left-panel {
  width: 320px;
  overflow-y: auto;
  padding: 12px;
  background: #f5f7fa;
}
.center-panel {
  flex: 1;
  overflow: hidden;
}
.right-panel {
  width: 340px;
  overflow-y: auto;
  padding: 12px;
  background: #f5f7fa;
}
.seo-result {
  .score-bar {
    text-align: center;
    .score-num { font-size: 42px; font-weight: 800; }
    .score-label { color: #909399; font-size: 14px; }
  }
  .check-item {
    margin-bottom: 10px;
    padding: 6px 0;
    border-bottom: 1px solid #f0f0f0;
    .check-header {
      display: flex;
      align-items: center;
      gap: 6px;
      .check-name { font-weight: 600; flex: 1; }
      .check-score { color: #909399; font-size: 12px; }
    }
    .check-msg { font-size: 12px; color: #606266; margin: 2px 0 0 22px; }
    .check-sugg { font-size: 12px; color: #909399; margin: 2px 0 0 22px; }
  }
  .ai-detect { font-size: 13px; margin-bottom: 12px; }
  .cwv-section {
    .cwv-grid {
      display: flex;
      gap: 8px;
      .cwv-item {
        flex: 1;
        text-align: center;
        background: #f5f7fa;
        border-radius: 4px;
        padding: 8px 4px;
        .cwv-label { font-size: 11px; color: #909399; }
        .cwv-val { font-size: 14px; font-weight: 700; }
        .cwv-low { color: #67c23a; }
        .cwv-medium { color: #e6a23c; }
        .cwv-high { color: #f56c6c; }
      }
    }
  }
}
</style>
