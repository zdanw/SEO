<template>
  <div class="page-container">
    <el-tabs v-model="activeTab" type="border-card" class="page-tabs">
      <!-- ============ Tab 1: 账号配置 ============ -->
      <el-tab-pane label="社交账号" name="accounts">
        <div style="margin-bottom: 12px">
          <el-button type="primary" @click="openAccountDialog()">
            <el-icon><Plus /></el-icon>&nbsp;添加账号
          </el-button>
        </div>
        <el-table :data="accounts" v-loading="accountLoading" stripe>
          <el-table-column prop="id" label="ID" width="60" />
          <el-table-column prop="platform" label="平台" width="120">
            <template #default="{ row }">
              <el-tag :type="platformTagType(row.platform)">{{ platformLabel(row.platform) }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="account_name" label="账号名称" min-width="160" />
          <el-table-column prop="is_active" label="状态" width="100" align="center">
            <template #default="{ row }">
              <el-tag :type="row.is_active ? 'success' : 'info'" size="small">
                {{ row.is_active ? '激活' : '禁用' }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="created_at" label="创建时间" width="170">
            <template #default="{ row }">{{ formatTime(row.created_at) }}</template>
          </el-table-column>
          <el-table-column label="操作" width="160" fixed="right">
            <template #default="{ row }">
              <el-button size="small" @click="openAccountDialog(row)">编辑</el-button>
              <el-popconfirm title="确认删除？" @confirm="handleDeleteAccount(row.id)">
                <template #reference>
                  <el-button size="small" type="danger">删除</el-button>
                </template>
              </el-popconfirm>
            </template>
          </el-table-column>
        </el-table>
      </el-tab-pane>

      <!-- ============ Tab 2: 发帖任务 ============ -->
      <el-tab-pane label="发帖任务" name="posts">
        <div class="filter-bar">
          <el-select v-model="postStatusFilter" placeholder="状态筛选" clearable style="width: 140px" @change="loadPosts">
            <el-option label="待发" value="pending" />
            <el-option label="已排期" value="scheduled" />
            <el-option label="发送中" value="posting" />
            <el-option label="已发送" value="posted" />
            <el-option label="失败" value="failed" />
            <el-option label="已取消" value="cancelled" />
          </el-select>
          <el-button @click="loadPosts">刷新</el-button>
          <el-button type="primary" @click="openPostDialog()">新建发帖任务</el-button>
        </div>
        <el-table :data="posts" v-loading="postLoading" stripe style="margin-top: 12px">
          <el-table-column prop="id" label="ID" width="60" />
          <el-table-column prop="platform" label="平台" width="100">
            <template #default="{ row }">
              <el-tag :type="platformTagType(row.platform)" size="small">{{ platformLabel(row.platform) }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="title" label="标题" min-width="200" show-overflow-tooltip />
          <el-table-column prop="status" label="状态" width="100" align="center">
            <template #default="{ row }">
              <el-tag :type="postStatusTagType(row.status)" size="small">{{ postStatusLabel(row.status) }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="scheduled_at" label="计划发送" width="170">
            <template #default="{ row }">{{ row.scheduled_at ? formatTime(row.scheduled_at) : '-' }}</template>
          </el-table-column>
          <el-table-column prop="posted_at" label="实际发送" width="170">
            <template #default="{ row }">{{ row.posted_at ? formatTime(row.posted_at) : '-' }}</template>
          </el-table-column>
          <el-table-column prop="platform_post_id" label="平台ID" width="160" show-overflow-tooltip>
            <template #default="{ row }">
              <span v-if="row.platform_post_id" style="font-family: monospace; font-size: 12px">{{ row.platform_post_id }}</span>
              <span v-else style="color: #c0c4cc">-</span>
            </template>
          </el-table-column>
          <el-table-column label="操作" width="220" fixed="right">
            <template #default="{ row }">
              <el-button
                v-if="!['posted', 'posting', 'cancelled'].includes(row.status)"
                size="small" type="success" :loading="sendingId === row.id"
                @click="handleSendNow(row.id)"
              >立即发送</el-button>
              <el-popconfirm
                v-if="!['posted', 'posting'].includes(row.status)"
                title="确认删除？" @confirm="handleDeletePost(row.id)"
              >
                <template #reference>
                  <el-button size="small" type="danger">删除</el-button>
                </template>
              </el-popconfirm>
              <el-tooltip v-if="row.error_message" :content="row.error_message" placement="top">
                <el-icon color="#f56c6c" size="16"><WarningFilled /></el-icon>
              </el-tooltip>
            </template>
          </el-table-column>
        </el-table>
      </el-tab-pane>

      <!-- ============ Tab 3: 自动分发 ============ -->
      <el-tab-pane label="自动分发" name="auto">
        <el-card shadow="never">
          <template #header><span style="font-weight: 600">文章发布 → 自动生成分发任务</span></template>
          <el-form label-position="top" style="max-width: 600px">
            <el-form-item label="选择文章">
              <el-select v-model="autoForm.article_id" placeholder="选择已发布的文章" filterable style="width: 100%">
                <el-option
                  v-for="a in articles"
                  :key="a.id"
                  :label="`[${a.id}] ${a.title} (${a.status})`"
                  :value="a.id"
                />
              </el-select>
            </el-form-item>
            <el-form-item label="延迟发送（分钟）">
              <el-input-number v-model="autoForm.delay_minutes" :min="0" :max="1440" :step="5" />
              <span style="color: #909399; font-size: 12px; margin-left: 12px">每个账号额外错峰 5 分钟</span>
            </el-form-item>
            <el-alert
              type="info"
              :closable="false"
              title="点击后将调用 AI 为每个激活账号生成差异化文案，并创建定时发帖任务"
              style="margin-bottom: 12px"
            />
            <el-button type="primary" :loading="autoLoading" @click="handleAutoDistribute">
              生成分发任务
            </el-button>
          </el-form>

          <div v-if="autoResult" style="margin-top: 20px">
            <el-divider />
            <div style="font-weight: 600; margin-bottom: 8px">生成结果（{{ autoResult.created_posts.length }} 条任务）</div>
            <el-table :data="autoResult.created_posts" size="small" stripe>
              <el-table-column prop="id" label="ID" width="60" />
              <el-table-column prop="account_id" label="账号ID" width="80" />
              <el-table-column prop="title" label="文案标题" min-width="200" show-overflow-tooltip />
              <el-table-column prop="status" label="状态" width="100">
                <template #default="{ row }">
                  <el-tag :type="postStatusTagType(row.status)" size="small">{{ postStatusLabel(row.status) }}</el-tag>
                </template>
              </el-table-column>
              <el-table-column prop="scheduled_at" label="计划发送" width="170">
                <template #default="{ row }">{{ row.scheduled_at ? formatTime(row.scheduled_at) : '-' }}</template>
              </el-table-column>
            </el-table>
          </div>
        </el-card>
      </el-tab-pane>

      <!-- ============ Tab 4: 社区互动 ============ -->
      <el-tab-pane label="社区互动" name="community">
        <el-card shadow="never">
          <template #header><span style="font-weight: 600">AI 生成社区评论草稿（供人工审核后发布）</span></template>
          <el-form label-position="top" style="max-width: 600px">
            <el-form-item label="选择文章">
              <el-select v-model="communityForm.article_id" placeholder="选择文章" filterable style="width: 100%">
                <el-option v-for="a in articles" :key="a.id" :label="`[${a.id}] ${a.title}`" :value="a.id" />
              </el-select>
            </el-form-item>
            <el-form-item label="目标平台">
              <el-checkbox-group v-model="communityForm.platforms">
                <el-checkbox value="Reddit">Reddit</el-checkbox>
                <el-checkbox value="PulseForge">PulseForge</el-checkbox>
                <el-checkbox value="Twitter/X">Twitter/X</el-checkbox>
              </el-checkbox-group>
            </el-form-item>
            <el-form-item label="每平台生成条数">
              <el-input-number v-model="communityForm.count" :min="1" :max="5" />
            </el-form-item>
            <el-button type="primary" :loading="communityLoading" @click="handleGenerateComments">
              生成评论草稿
            </el-button>
          </el-form>

          <div v-if="communityResult && communityResult.comments.length" style="margin-top: 20px">
            <el-divider />
            <div v-for="(c, i) in communityResult.comments" :key="i" class="comment-card">
              <div class="comment-header">
                <el-tag size="small" type="warning">{{ c.platform }}</el-tag>
                <span class="comment-tone">语气：{{ c.tone }}</span>
                <el-button size="small" @click="copyComment(c.comment)">复制</el-button>
              </div>
              <div class="comment-body">{{ c.comment }}</div>
            </div>
          </div>
        </el-card>
      </el-tab-pane>
    </el-tabs>

    <!-- ============ 账号编辑弹窗 ============ -->
    <el-dialog v-model="accountDialog" :title="accountForm.id ? '编辑账号' : '添加账号'" width="480px">
      <el-form label-position="top" :model="accountForm">
        <el-form-item label="平台" required>
          <el-select v-model="accountForm.platform" style="width: 100%" :disabled="!!accountForm.id">
            <el-option label="PulseForge" value="pulseforge" />
            <el-option label="LinkedIn" value="linkedin" />
            <el-option label="Twitter/X" value="twitter" />
            <el-option label="Facebook" value="facebook" />
            <el-option label="Reddit" value="reddit" />
          </el-select>
        </el-form-item>
        <el-form-item label="账号名称" required>
          <el-input v-model="accountForm.account_name" />
        </el-form-item>
        <el-form-item label="Access Token">
          <el-input v-model="accountForm.access_token" type="password" show-password placeholder="平台 API Token" />
        </el-form-item>
        <el-form-item label="激活">
          <el-switch v-model="accountForm.is_active" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="accountDialog = false">取消</el-button>
        <el-button type="primary" :loading="accountSaving" @click="handleSaveAccount">保存</el-button>
      </template>
    </el-dialog>

    <!-- ============ 发帖任务弹窗 ============ -->
    <el-dialog v-model="postDialog" title="新建发帖任务" width="600px">
      <el-form label-position="top" :model="postForm">
        <el-form-item label="社交账号" required>
          <el-select v-model="postForm.account_id" placeholder="选择账号" style="width: 100%">
            <el-option
              v-for="a in accounts"
              :key="a.id"
              :label="`${platformLabel(a.platform)} - ${a.account_name}`"
              :value="a.id"
              :disabled="!a.is_active"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="关联文章">
          <el-select v-model="postForm.article_id" placeholder="选择文章（可选）" filterable clearable style="width: 100%">
            <el-option v-for="a in articles" :key="a.id" :label="`[${a.id}] ${a.title}`" :value="a.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="标题">
          <el-input v-model="postForm.title" />
        </el-form-item>
        <el-form-item label="摘要正文">
          <el-input v-model="postForm.summary" type="textarea" :rows="4" />
        </el-form-item>
        <el-form-item label="外部链接">
          <el-input v-model="postForm.external_url" placeholder="https://..." />
        </el-form-item>
        <el-form-item label="Hashtags">
          <el-input v-model="postForm.hashtags_str" placeholder="#Python #asyncio（空格分隔）" />
        </el-form-item>
        <el-form-item label="计划发送时间">
          <el-date-picker
            v-model="postForm.scheduled_at"
            type="datetime"
            placeholder="留空 = 立即发送"
            format="YYYY-MM-DD HH:mm"
            value-format="YYYY-MM-DDTHH:mm:ss"
            style="width: 100%"
          />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="postDialog = false">取消</el-button>
        <el-button type="primary" :loading="postSaving" @click="handleSavePost">创建</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { Plus, WarningFilled } from '@element-plus/icons-vue'
import {
  listAccounts, createAccount, updateAccount, deleteAccount,
  listPosts, createPost, deletePost, sendPostNow,
  autoDistribute, generateCommentDrafts,
  type SocialAccount, type SocialPost, type AutoDistributeResponse,
  type CommunityCommentsResponse,
} from '@/api/social'
import { listArticles, type Article } from '@/api/articles'

const activeTab = ref('accounts')
const accounts = ref<SocialAccount[]>([])
const posts = ref<SocialPost[]>([])
const articles = ref<Article[]>([])
const accountLoading = ref(false)
const postLoading = ref(false)
const postStatusFilter = ref('')
const sendingId = ref<number | null>(null)
const autoLoading = ref(false)
const autoResult = ref<AutoDistributeResponse | null>(null)
const communityLoading = ref(false)
const communityResult = ref<CommunityCommentsResponse | null>(null)

// 账号弹窗
const accountDialog = ref(false)
const accountSaving = ref(false)
const accountForm = reactive<{id?: number; platform: string; account_name: string; access_token: string; is_active: boolean}>({
  platform: 'pulseforge',
  account_name: '',
  access_token: '',
  is_active: true,
})

// 发帖弹窗
const postDialog = ref(false)
const postSaving = ref(false)
const postForm = reactive<{
  account_id: number | null; article_id: number | null;
  title: string; summary: string; external_url: string;
  hashtags_str: string; scheduled_at: string;
}>({
  account_id: null, article_id: null,
  title: '', summary: '', external_url: '',
  hashtags_str: '', scheduled_at: '',
})

// 自动分发表单
const autoForm = reactive<{ article_id: number | null; delay_minutes: number }>({
  article_id: null, delay_minutes: 30,
})

// 社区互动表单
const communityForm = reactive<{ article_id: number | null; platforms: string[]; count: number }>({
  article_id: null, platforms: ['Reddit', 'PulseForge'], count: 3,
})

onMounted(async () => {
  await Promise.all([loadAccounts(), loadPosts(), loadArticles()])
})

async function loadAccounts() {
  accountLoading.value = true
  try { accounts.value = await listAccounts() } finally { accountLoading.value = false }
}
async function loadPosts() {
  postLoading.value = true
  try {
    posts.value = await listPosts(postStatusFilter.value ? { status: postStatusFilter.value } : undefined)
  } finally { postLoading.value = false }
}
async function loadArticles() {
  articles.value = await listArticles({ size: 100 })
}

// ============ 账号操作 ============
function openAccountDialog(acc?: SocialAccount) {
  if (acc) {
    accountForm.id = acc.id
    accountForm.platform = acc.platform
    accountForm.account_name = acc.account_name
    accountForm.access_token = acc.access_token || ''
    accountForm.is_active = acc.is_active
  } else {
    accountForm.id = undefined
    accountForm.platform = 'pulseforge'
    accountForm.account_name = ''
    accountForm.access_token = ''
    accountForm.is_active = true
  }
  accountDialog.value = true
}

async function handleSaveAccount() {
  if (!accountForm.account_name) { ElMessage.warning('请填写账号名称'); return }
  accountSaving.value = true
  try {
    const payload = {
      platform: accountForm.platform as any,
      account_name: accountForm.account_name,
      access_token: accountForm.access_token || undefined,
      is_active: accountForm.is_active,
    }
    if (accountForm.id) {
      await updateAccount(accountForm.id, payload)
      ElMessage.success('已更新')
    } else {
      await createAccount(payload)
      ElMessage.success('已创建')
    }
    accountDialog.value = false
    loadAccounts()
  } finally { accountSaving.value = false }
}

async function handleDeleteAccount(id: number) {
  await deleteAccount(id)
  ElMessage.success('已删除')
  loadAccounts()
}

// ============ 发帖操作 ============
function openPostDialog() {
  postForm.account_id = null
  postForm.article_id = null
  postForm.title = ''
  postForm.summary = ''
  postForm.external_url = ''
  postForm.hashtags_str = ''
  postForm.scheduled_at = ''
  postDialog.value = true
}

async function handleSavePost() {
  if (!postForm.account_id) { ElMessage.warning('请选择社交账号'); return }
  postSaving.value = true
  try {
    const hashtags = postForm.hashtags_str
      ? postForm.hashtags_str.split(/\s+/).filter((s: string) => s.trim())
      : undefined
    await createPost({
      account_id: postForm.account_id,
      article_id: postForm.article_id || undefined,
      title: postForm.title || undefined,
      summary: postForm.summary || undefined,
      external_url: postForm.external_url || undefined,
      hashtags,
      scheduled_at: postForm.scheduled_at || undefined,
    })
    ElMessage.success('已创建发帖任务')
    postDialog.value = false
    loadPosts()
  } finally { postSaving.value = false }
}

async function handleSendNow(id: number) {
  sendingId.value = id
  try {
    await sendPostNow(id)
    ElMessage.success('已发送')
    loadPosts()
  } finally { sendingId.value = null }
}

async function handleDeletePost(id: number) {
  await deletePost(id)
  ElMessage.success('已删除')
  loadPosts()
}

// ============ 自动分发 ============
async function handleAutoDistribute() {
  if (!autoForm.article_id) { ElMessage.warning('请选择文章'); return }
  autoLoading.value = true
  try {
    autoResult.value = await autoDistribute({
      article_id: autoForm.article_id,
      delay_minutes: autoForm.delay_minutes,
    })
    ElMessage.success(`已生成 ${autoResult.value.created_posts.length} 条分发任务`)
    loadPosts()
  } finally { autoLoading.value = false }
}

// ============ 社区互动 ============
async function handleGenerateComments() {
  if (!communityForm.article_id) { ElMessage.warning('请选择文章'); return }
  if (!communityForm.platforms.length) { ElMessage.warning('请至少选一个平台'); return }
  communityLoading.value = true
  try {
    communityResult.value = await generateCommentDrafts(
      communityForm.article_id,
      communityForm.platforms,
      communityForm.count,
    )
    ElMessage.success(`已生成 ${communityResult.value.comments.length} 条草稿`)
  } finally { communityLoading.value = false }
}

function copyComment(text: string) {
  navigator.clipboard.writeText(text)
  ElMessage.success('已复制到剪贴板')
}

// ============ 工具 ============
function platformLabel(p: string): string {
  return { pulseforge: 'PulseForge', linkedin: 'LinkedIn', twitter: 'Twitter/X', facebook: 'Facebook', reddit: 'Reddit' }[p] || p
}
function platformTagType(p: string): any {
  return { pulseforge: 'primary', linkedin: 'success', twitter: 'info', facebook: 'warning', reddit: 'danger' }[p] || ''
}
function postStatusLabel(s: string): string {
  return { pending: '待发', scheduled: '已排期', posting: '发送中', posted: '已发送', failed: '失败', cancelled: '已取消' }[s] || s
}
function postStatusTagType(s: string): any {
  return { pending: 'info', scheduled: 'warning', posting: 'primary', posted: 'success', failed: 'danger', cancelled: 'info' }[s] || ''
}
function formatTime(t: string): string {
  return new Date(t).toLocaleString('zh-CN')
}
</script>

<style lang="scss" scoped>
.filter-bar {
  display: flex;
  gap: 12px;
  align-items: center;
}
.comment-card {
  background: #f5f7fa;
  border-radius: 6px;
  padding: 12px;
  margin-bottom: 12px;
  .comment-header {
    display: flex;
    align-items: center;
    gap: 10px;
    margin-bottom: 8px;
    .comment-tone { color: #909399; font-size: 12px; flex: 1; }
  }
  .comment-body {
    font-size: 14px;
    line-height: 1.6;
    color: #303133;
    white-space: pre-wrap;
  }
}
</style>
