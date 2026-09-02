<template>
  <div class="page-container">
    <el-tabs v-model="activeTab">
      <el-tab-pane label="单页检查" name="single">
    <el-row :gutter="16">
      <el-col :xs="24" :lg="10">
        <el-card shadow="never" class="content-card">
          <template #header><span style="font-weight: 600">文章内容输入</span></template>
          <el-form label-position="top" size="default">
            <el-form-item label="选择已入库文章（可选）">
              <el-select v-model="selectedArticleId" placeholder="选择文章自动填充" filterable clearable style="width: 100%" @change="onArticleSelect">
                <el-option v-for="a in articles" :key="a.id" :label="`[${a.id}] ${a.title}`" :value="a.id" />
              </el-select>
            </el-form-item>
            <el-form-item label="标题">
              <el-input v-model="form.title" />
            </el-form-item>
            <el-form-item label="关键词">
              <el-input v-model="form.keyword" placeholder="目标关键词" />
            </el-form-item>
            <el-form-item label="Meta Description">
              <el-input v-model="form.meta_description" type="textarea" :rows="2" maxlength="320" show-word-limit />
            </el-form-item>
            <el-form-item label="正文 (Markdown)">
              <el-input v-model="form.content" type="textarea" :rows="12" placeholder="# 标题&#10;正文内容..." />
            </el-form-item>
            <el-form-item label="封面图 URL">
              <el-input v-model="form.cover_image_url" />
            </el-form-item>
            <el-button type="primary" :loading="loading" style="width: 100%" @click="runCheck">
              运行 SEO 检查
            </el-button>
          </el-form>
        </el-card>
      </el-col>

      <!-- 右侧：结果区 -->
      <el-col :xs="24" :lg="14">
        <el-card shadow="never" class="content-card">
          <template #header>
            <div style="display: flex; justify-content: space-between; align-items: center">
              <span style="font-weight: 600">检查结果</span>
              <span v-if="result" class="total-score" :style="{ color: scoreColor(result.total_score) }">
                总分：{{ result.total_score.toFixed(1) }} / 100
              </span>
            </div>
          </template>

          <div v-if="result">
            <!-- 检查项列表 -->
            <div v-for="item in result.items" :key="item.key" class="check-row">
              <el-icon :color="statusColor(item.status)" :size="18">
                <CircleCheck v-if="item.status === 'pass'" />
                <WarningFilled v-else-if="item.status === 'warning'" />
                <CircleCloseFilled v-else />
              </el-icon>
              <div class="check-body">
                <div class="check-top">
                  <span class="check-title">{{ item.name }}</span>
                  <el-progress
                    :percentage="Math.round((item.score / item.max_score) * 100)"
                    :color="statusColor(item.status)"
                    :stroke-width="10"
                    style="width: 120px"
                  />
                  <span class="check-pts">{{ item.score }}/{{ item.max_score }}</span>
                </div>
                <div class="check-msg">{{ item.message }}</div>
                <div v-if="item.suggestion" class="check-sugg">建议：{{ item.suggestion }}</div>
              </div>
            </div>

            <el-divider />

            <div class="extra-section">
              <div class="section-label">AI 内容检测</div>
              <AiDetectDetail
                :score="result.ai_detected_score"
                :detail="result.ai_detail"
              />
            </div>

            <SerpScoreDetail :detail="result.serp_detail" />

            <TechnicalAuditDetail :audit="result.technical_audit" />

            <!-- CWV -->
            <div v-if="result.cwv_estimate?.lcp_ms != null || result.cwv_estimate?.note" class="extra-section">
              <div class="section-label">
                Core Web Vitals
                <el-tag v-if="result.cwv_estimate.source === 'psi'" type="success" size="small" style="margin-left: 8px">
                  PageSpeed 实测
                </el-tag>
                <el-tag v-else-if="result.cwv_estimate.source === 'estimated'" type="info" size="small" style="margin-left: 8px">
                  静态预估
                </el-tag>
                <el-tag v-if="result.cwv_estimate.performance_score != null" size="small" style="margin-left: 6px">
                  性能 {{ result.cwv_estimate.performance_score }}
                </el-tag>
              </div>
              <div v-if="result.cwv_estimate.lcp_ms != null" class="cwv-cards">
                <div :class="['cwv-card', riskClass(result.cwv_estimate.lcp_risk)]">
                  <div class="cwv-name">LCP</div>
                  <div class="cwv-value">{{ result.cwv_estimate.lcp_ms }}<span class="unit">ms</span></div>
                  <div class="cwv-risk">{{ result.cwv_estimate.lcp_risk }}</div>
                  <div class="cwv-desc">最大内容渲染</div>
                </div>
                <div :class="['cwv-card', riskClass(result.cwv_estimate.cls_risk)]">
                  <div class="cwv-name">CLS</div>
                  <div class="cwv-value">{{ result.cwv_estimate.cls }}</div>
                  <div class="cwv-risk">{{ result.cwv_estimate.cls_risk }}</div>
                  <div class="cwv-desc">累积布局偏移</div>
                </div>
                <div :class="['cwv-card', riskClass(result.cwv_estimate.inp_risk)]">
                  <div class="cwv-name">INP</div>
                  <div class="cwv-value">{{ result.cwv_estimate.inp_ms }}<span class="unit">ms</span></div>
                  <div class="cwv-risk">{{ result.cwv_estimate.inp_risk }}</div>
                  <div class="cwv-desc">交互延迟</div>
                </div>
              </div>
              <el-alert type="info" :closable="false" :title="result.cwv_estimate.note" style="margin-top: 8px" />
            </div>

            <!-- 内链推荐 -->
            <div v-if="result.internal_links_recommended && result.internal_links_recommended.length" class="extra-section">
              <div class="section-label">站内内链推荐</div>
              <el-table :data="result.internal_links_recommended" size="small" stripe>
                <el-table-column prop="target_title" label="目标文章" min-width="200" show-overflow-tooltip />
                <el-table-column prop="anchor_text" label="推荐锚文本" width="180" />
                <el-table-column prop="relevance_score" label="相关度" width="100" align="center">
                  <template #default="{ row }">{{ (row.relevance_score * 100).toFixed(1) }}%</template>
                </el-table-column>
              </el-table>
            </div>
          </div>
          <el-empty v-else description="运行检查后将在此显示详细结果" />
        </el-card>
      </el-col>
    </el-row>
      </el-tab-pane>

      <el-tab-pane label="URL 抓取检查" name="url">
        <el-card shadow="never" class="content-card">
          <el-form inline>
            <el-form-item label="客户站 URL">
              <el-input v-model="urlForm.url" placeholder="https://client.com/page" style="width: 360px" />
            </el-form-item>
            <el-form-item label="关键词">
              <el-input v-model="urlForm.keyword" style="width: 160px" />
            </el-form-item>
            <el-button type="primary" :loading="urlLoading" @click="runUrlCheck">抓取并检查</el-button>
          </el-form>
        </el-card>
      </el-tab-pane>

      <el-tab-pane label="批量审计" name="audit">
        <el-card shadow="never" class="content-card">
          <div style="margin-bottom: 12px">
            <el-input-number v-model="auditMaxPages" :min="1" :max="200" />
            <el-button type="primary" :loading="auditLoading" style="margin-left: 12px" @click="runAudit">
              扫描 Sitemap 并审计
            </el-button>
          </div>
          <el-table v-if="audits.length" :data="audits" stripe>
            <el-table-column prop="id" label="ID" width="70" />
            <el-table-column prop="status" label="状态" width="100" />
            <el-table-column prop="scanned_pages" label="已扫描" width="90" />
            <el-table-column prop="avg_score" label="平均分" width="90" />
            <el-table-column prop="summary" label="摘要" show-overflow-tooltip />
            <el-table-column prop="created_at" label="时间" width="180" />
          </el-table>
          <el-empty v-else description="暂无审计记录" />
        </el-card>
      </el-tab-pane>
    </el-tabs>
  </div>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage } from 'element-plus'
import { CircleCheck, WarningFilled, CircleCloseFilled } from '@element-plus/icons-vue'
import { listArticles, getArticle, type Article } from '@/api/articles'
import { checkSeo, checkUrlSeo, createSeoAudit, listSeoAudits, type SeoAudit, type SeoCheckResult } from '@/api/seo'
import AiDetectDetail from '@/components/AiDetectDetail.vue'
import SerpScoreDetail from '@/components/SerpScoreDetail.vue'
import TechnicalAuditDetail from '@/components/TechnicalAuditDetail.vue'

const route = useRoute()
const activeTab = ref('single')
const articles = ref<Article[]>([])
const selectedArticleId = ref<number | null>(null)
const loading = ref(false)
const urlLoading = ref(false)
const auditLoading = ref(false)
const auditMaxPages = ref(50)
const audits = ref<SeoAudit[]>([])
const result = ref<SeoCheckResult | null>(null)

const urlForm = reactive({ url: '', keyword: '' })

const form = reactive({
  title: '',
  keyword: '',
  meta_description: '',
  content: '',
  cover_image_url: '',
})

onMounted(async () => {
  articles.value = await listArticles()
  audits.value = await listSeoAudits().catch(() => [])
  const qId = route.query.article_id
  if (qId) {
    selectedArticleId.value = Number(qId)
    await onArticleSelect(Number(qId))
  }
})

async function onArticleSelect(id: number | null) {
  if (!id) return
  const art = await getArticle(id)
  form.title = art.title
  form.meta_description = art.meta_description || ''
  form.content = art.content || ''
  form.cover_image_url = art.cover_image_url || ''
}

async function runUrlCheck() {
  if (!urlForm.url) {
    ElMessage.warning('请填写 URL')
    return
  }
  urlLoading.value = true
  try {
    result.value = await checkUrlSeo(urlForm.url, urlForm.keyword || undefined)
    activeTab.value = 'single'
    ElMessage.success('URL 检查完成')
  } finally {
    urlLoading.value = false
  }
}

async function runAudit() {
  auditLoading.value = true
  try {
    const audit = await createSeoAudit(auditMaxPages.value)
    audits.value = [audit, ...audits.value]
    ElMessage.success('批量审计完成')
  } finally {
    auditLoading.value = false
  }
}

async function runCheck() {
  if (!form.title) {
    ElMessage.warning('请填写标题')
    return
  }
  loading.value = true
  try {
    result.value = await checkSeo({
      title: form.title,
      content: form.content,
      meta_description: form.meta_description,
      keyword: form.keyword,
      cover_image_url: form.cover_image_url,
    })
  } finally {
    loading.value = false
  }
}

function statusColor(s: string): string {
  return { pass: '#67c23a', warning: '#e6a23c', fail: '#f56c6c' }[s] || '#909399'
}
function scoreColor(score: number): string {
  return score >= 70 ? '#67c23a' : score >= 40 ? '#e6a23c' : '#f56c6c'
}
function riskClass(risk: string): string {
  return `cwv-${risk}`
}
</script>

<style lang="scss" scoped>
.total-score {
  font-size: 18px;
  font-weight: 700;
}
.check-row {
  display: flex;
  gap: 10px;
  padding: 10px 0;
  border-bottom: 1px solid #f0f0f0;
  .check-body { flex: 1; }
  .check-top {
    display: flex;
    align-items: center;
    gap: 10px;
    .check-title { font-weight: 600; min-width: 130px; }
    .check-pts { color: #909399; font-size: 12px; min-width: 50px; text-align: right; }
  }
  .check-msg { font-size: 13px; color: #606266; margin-top: 4px; }
  .check-sugg { font-size: 12px; color: #e6a23c; margin-top: 2px; }
}
.extra-section {
  margin-top: 16px;
  .section-label {
    display: block;
    font-weight: 600;
    margin-bottom: 10px;
  }
}
.cwv-cards {
  display: flex;
  gap: 12px;
  .cwv-card {
    flex: 1;
    text-align: center;
    border-radius: 8px;
    padding: 14px 8px;
    background: #f5f7fa;
    .cwv-name { font-size: 12px; color: #909399; }
    .cwv-value { font-size: 22px; font-weight: 800; margin: 4px 0; .unit { font-size: 12px; font-weight: 400; } }
    .cwv-risk { font-size: 11px; text-transform: uppercase; }
    .cwv-desc { font-size: 11px; color: #c0c4cc; margin-top: 2px; }
  }
  .cwv-low { background: #f0f9eb; .cwv-value, .cwv-risk { color: #67c23a; } }
  .cwv-medium { background: #fdf6ec; .cwv-value, .cwv-risk { color: #e6a23c; } }
  .cwv-high { background: #fef0f0; .cwv-value, .cwv-risk { color: #f56c6c; } }
}
</style>
