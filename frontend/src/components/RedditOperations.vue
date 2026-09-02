<template>
  <div class="reddit-ops">
    <!-- 账号连接 -->
    <el-card shadow="never" class="section-card">
      <template #header><span class="card-title">Reddit 账号</span></template>
      <div class="account-bar">
        <el-tag v-if="status?.configured" type="success" size="small">API 已配置</el-tag>
        <el-tag v-else type="warning" size="small">未配置 REDDIT_CLIENT_ID（Mock 模式）</el-tag>
        <el-tag v-if="status?.connected" type="success" size="small">已连接</el-tag>
        <el-button type="primary" :loading="connecting" @click="handleConnect">连接 Reddit</el-button>
        <el-button @click="loadStatus">刷新</el-button>
      </div>
      <el-select v-model="selectedAccountId" placeholder="选择 Reddit 账号" style="width: 320px; margin-top: 12px">
        <el-option
          v-for="a in status?.accounts || []"
          :key="a.id"
          :label="a.account_name"
          :value="a.id"
        />
      </el-select>
    </el-card>

    <el-tabs v-model="innerTab" type="card" style="margin-top: 16px">
      <!-- 发帖 -->
      <el-tab-pane label="发帖审核" name="posts">
        <el-row :gutter="16">
          <el-col :span="10">
            <el-form label-position="top">
              <el-form-item label="帖子类型">
                <el-radio-group v-model="postForm.post_type">
                  <el-radio value="consultation">咨询帖</el-radio>
                  <el-radio value="experience">经验分享</el-radio>
                </el-radio-group>
              </el-form-item>
              <el-form-item label="子版块">
                <el-input v-model="postForm.subreddit" placeholder="BabyBumps（不含 r/）" />
              </el-form-item>
              <el-form-item label="主题关键词">
                <el-input v-model="postForm.keyword" placeholder="baby monitor" />
              </el-form-item>
              <el-form-item v-if="postForm.post_type === 'experience'" label="带入站点链接">
                <el-switch v-model="postForm.include_site_url" />
              </el-form-item>
              <el-button type="primary" :loading="postGenerating" :disabled="!selectedAccountId" @click="handleGeneratePost">
                AI 生成并入审核队列
              </el-button>
            </el-form>
          </el-col>
          <el-col :span="14">
            <div class="list-header">
              <span>审核队列</span>
              <el-select v-model="postStatusFilter" clearable placeholder="状态" style="width: 130px" @change="loadPosts">
                <el-option label="待审核" value="pending_review" />
                <el-option label="已批准" value="approved" />
                <el-option label="已发布" value="posted" />
                <el-option label="失败" value="failed" />
              </el-select>
            </div>
            <el-table :data="posts" v-loading="postsLoading" size="small" stripe max-height="480">
              <el-table-column prop="id" label="ID" width="50" />
              <el-table-column prop="post_type" label="类型" width="80">
                <template #default="{ row }">{{ row.post_type === 'consultation' ? '咨询' : '经验' }}</template>
              </el-table-column>
              <el-table-column prop="title" label="标题" min-width="160" show-overflow-tooltip />
              <el-table-column prop="status" label="状态" width="90">
                <template #default="{ row }">
                  <el-tag size="small" :type="statusTag(row.status)">{{ statusLabel(row.status) }}</el-tag>
                </template>
              </el-table-column>
              <el-table-column label="操作" width="220" fixed="right">
                <template #default="{ row }">
                  <el-button size="small" @click="openPostEdit(row)">编辑</el-button>
                  <el-button v-if="row.status === 'pending_review'" size="small" type="warning" @click="approvePost(row.id)">批准</el-button>
                  <el-button v-if="row.status === 'approved'" size="small" type="success" :loading="publishingPostId === row.id" @click="publishPost(row.id)">发布</el-button>
                </template>
              </el-table-column>
            </el-table>
          </el-col>
        </el-row>
      </el-tab-pane>

      <!-- 评论 -->
      <el-tab-pane label="评论审核" name="comments">
        <el-tabs v-model="commentInputTab" type="border-card">
          <el-tab-pane label="粘贴 URL" name="url">
            <el-form label-position="top" style="max-width: 560px">
              <el-form-item label="目标帖子 URL">
                <el-input v-model="commentUrlForm.target_post_url" placeholder="https://www.reddit.com/r/.../comments/..." />
              </el-form-item>
              <el-form-item label="评论中带站点链接">
                <el-switch v-model="commentUrlForm.include_site_url" />
              </el-form-item>
              <el-button type="primary" :loading="commentGenerating" :disabled="!selectedAccountId" @click="handleGenerateCommentUrl">
                AI 生成评论
              </el-button>
            </el-form>
          </el-tab-pane>
          <el-tab-pane label="关键词搜索" name="search">
            <el-form label-position="top" style="max-width: 560px">
              <el-form-item label="子版块">
                <el-input v-model="commentSearchForm.subreddit" placeholder="BabyBumps" />
              </el-form-item>
              <el-form-item label="关键词">
                <el-input v-model="commentSearchForm.keyword" placeholder="baby monitor" />
              </el-form-item>
              <el-button :loading="searchLoading" @click="handleSearch">搜索帖子</el-button>
            </el-form>
            <el-table
              v-if="searchResults.length"
              :data="searchResults"
              size="small"
              style="margin-top: 12px"
              @selection-change="onSearchSelect"
            >
              <el-table-column type="selection" width="40" />
              <el-table-column prop="title" label="标题" min-width="200" show-overflow-tooltip />
              <el-table-column prop="score" label="Score" width="70" />
              <el-table-column prop="num_comments" label="评论" width="70" />
            </el-table>
            <el-button
              v-if="selectedSearchUrls.length"
              type="primary"
              style="margin-top: 8px"
              :loading="commentGenerating"
              :disabled="!selectedAccountId"
              @click="handleGenerateCommentBatch"
            >
              为选中 {{ selectedSearchUrls.length }} 帖生成评论
            </el-button>
          </el-tab-pane>
        </el-tabs>

        <el-divider />
        <div class="list-header">
          <span>评论审核队列</span>
          <el-select v-model="commentStatusFilter" clearable placeholder="状态" style="width: 130px" @change="loadComments">
            <el-option label="待审核" value="pending_review" />
            <el-option label="已批准" value="approved" />
            <el-option label="已发布" value="posted" />
          </el-select>
        </div>
        <el-table :data="comments" v-loading="commentsLoading" size="small" stripe max-height="360">
          <el-table-column prop="id" label="ID" width="50" />
          <el-table-column prop="target_post_title" label="目标帖" min-width="160" show-overflow-tooltip />
          <el-table-column prop="body" label="评论预览" min-width="200" show-overflow-tooltip />
          <el-table-column prop="status" label="状态" width="90">
            <template #default="{ row }">
              <el-tag size="small" :type="statusTag(row.status)">{{ statusLabel(row.status) }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column label="操作" width="220" fixed="right">
            <template #default="{ row }">
              <el-button size="small" @click="openCommentEdit(row)">编辑</el-button>
              <el-button v-if="row.status === 'pending_review'" size="small" type="warning" @click="approveComment(row.id)">批准</el-button>
              <el-button v-if="row.status === 'approved'" size="small" type="success" :loading="publishingCommentId === row.id" @click="publishComment(row.id)">发布</el-button>
            </template>
          </el-table-column>
        </el-table>
      </el-tab-pane>
    </el-tabs>

    <!-- 编辑帖子弹窗 -->
    <el-dialog v-model="postEditDialog" title="编辑 Reddit 帖子" width="640px">
      <el-form label-position="top">
        <el-form-item label="标题"><el-input v-model="postEditForm.title" /></el-form-item>
        <el-form-item label="子版块"><el-input v-model="postEditForm.subreddit" /></el-form-item>
        <el-form-item label="正文"><el-input v-model="postEditForm.body" type="textarea" :rows="10" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="postEditDialog = false">取消</el-button>
        <el-button type="primary" :loading="postSaving" @click="savePostEdit">保存</el-button>
      </template>
    </el-dialog>

    <!-- 编辑评论弹窗 -->
    <el-dialog v-model="commentEditDialog" title="编辑 Reddit 评论" width="560px">
      <el-input v-model="commentEditForm.body" type="textarea" :rows="8" />
      <template #footer>
        <el-button @click="commentEditDialog = false">取消</el-button>
        <el-button type="primary" :loading="commentSaving" @click="saveCommentEdit">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage } from 'element-plus'
import {
  getRedditStatus, startRedditOAuth,
  generateRedditPost, listRedditPosts, updateRedditPost, approveRedditPost, publishRedditPost,
  generateRedditComment, generateRedditCommentBatch, listRedditComments,
  updateRedditComment, approveRedditComment, publishRedditComment,
  searchRedditPosts,
  type RedditStatus, type RedditPost, type RedditComment, type RedditDiscoverItem,
  type PostType,
} from '@/api/reddit'

const route = useRoute()
const status = ref<RedditStatus | null>(null)
const selectedAccountId = ref<number | null>(null)
const connecting = ref(false)
const innerTab = ref('posts')

const postForm = reactive<{ post_type: PostType; subreddit: string; keyword: string; include_site_url: boolean }>({
  post_type: 'consultation', subreddit: '', keyword: '', include_site_url: false,
})
const posts = ref<RedditPost[]>([])
const postsLoading = ref(false)
const postGenerating = ref(false)
const postStatusFilter = ref('')
const publishingPostId = ref<number | null>(null)

const commentInputTab = ref('url')
const commentUrlForm = reactive({ target_post_url: '', include_site_url: false })
const commentSearchForm = reactive({ subreddit: '', keyword: '' })
const searchResults = ref<RedditDiscoverItem[]>([])
const selectedSearchUrls = ref<string[]>([])
const searchLoading = ref(false)
const comments = ref<RedditComment[]>([])
const commentsLoading = ref(false)
const commentGenerating = ref(false)
const commentStatusFilter = ref('')
const publishingCommentId = ref<number | null>(null)

const postEditDialog = ref(false)
const postEditForm = reactive({ id: 0, title: '', body: '', subreddit: '' })
const postSaving = ref(false)
const commentEditDialog = ref(false)
const commentEditForm = reactive({ id: 0, body: '' })
const commentSaving = ref(false)

onMounted(async () => {
  if (route.query.reddit === 'connected') ElMessage.success('Reddit 账号已连接')
  if (route.query.reddit === 'error') ElMessage.error(`Reddit 连接失败：${route.query.message || ''}`)
  await loadStatus()
  await Promise.all([loadPosts(), loadComments()])
})

async function loadStatus() {
  status.value = await getRedditStatus()
  if (!selectedAccountId.value && status.value.accounts.length) {
    selectedAccountId.value = status.value.accounts[0].id
  }
}

async function handleConnect() {
  connecting.value = true
  try {
    const { auth_url } = await startRedditOAuth()
    window.location.href = auth_url
  } finally { connecting.value = false }
}

async function loadPosts() {
  postsLoading.value = true
  try {
    posts.value = await listRedditPosts(postStatusFilter.value ? { status: postStatusFilter.value } : undefined)
  } finally { postsLoading.value = false }
}

async function handleGeneratePost() {
  if (!selectedAccountId.value) { ElMessage.warning('请选择 Reddit 账号'); return }
  if (!postForm.subreddit || !postForm.keyword) { ElMessage.warning('请填写子版块和关键词'); return }
  postGenerating.value = true
  try {
    await generateRedditPost({ account_id: selectedAccountId.value, ...postForm })
    ElMessage.success('已生成，请在审核队列中编辑/批准')
    loadPosts()
  } finally { postGenerating.value = false }
}

function openPostEdit(row: RedditPost) {
  postEditForm.id = row.id
  postEditForm.title = row.title
  postEditForm.body = row.body
  postEditForm.subreddit = row.subreddit
  postEditDialog.value = true
}

async function savePostEdit() {
  postSaving.value = true
  try {
    await updateRedditPost(postEditForm.id, {
      title: postEditForm.title, body: postEditForm.body, subreddit: postEditForm.subreddit,
    })
    ElMessage.success('已保存')
    postEditDialog.value = false
    loadPosts()
  } finally { postSaving.value = false }
}

async function approvePost(id: number) {
  await approveRedditPost(id)
  ElMessage.success('已批准')
  loadPosts()
}

async function publishPost(id: number) {
  publishingPostId.value = id
  try {
    const r = await publishRedditPost(id)
    ElMessage.success(r.status === 'posted' ? '发布成功' : `发布失败：${r.error_message}`)
    loadPosts()
  } finally { publishingPostId.value = null }
}

async function loadComments() {
  commentsLoading.value = true
  try {
    comments.value = await listRedditComments(commentStatusFilter.value ? { status: commentStatusFilter.value } : undefined)
  } finally { commentsLoading.value = false }
}

async function handleGenerateCommentUrl() {
  if (!selectedAccountId.value || !commentUrlForm.target_post_url) {
    ElMessage.warning('请选择账号并填写 URL'); return
  }
  commentGenerating.value = true
  try {
    await generateRedditComment({ account_id: selectedAccountId.value, ...commentUrlForm })
    ElMessage.success('评论已生成')
    loadComments()
  } finally { commentGenerating.value = false }
}

async function handleSearch() {
  if (!commentSearchForm.subreddit || !commentSearchForm.keyword) {
    ElMessage.warning('请填写子版块和关键词'); return
  }
  searchLoading.value = true
  try {
    const res = await searchRedditPosts(commentSearchForm)
    searchResults.value = res.items
  } finally { searchLoading.value = false }
}

function onSearchSelect(rows: RedditDiscoverItem[]) {
  selectedSearchUrls.value = rows.map(r => r.url)
}

async function handleGenerateCommentBatch() {
  if (!selectedAccountId.value) return
  commentGenerating.value = true
  try {
    await generateRedditCommentBatch({
      account_id: selectedAccountId.value,
      subreddit: commentSearchForm.subreddit,
      keyword: commentSearchForm.keyword,
      post_urls: selectedSearchUrls.value,
    })
    ElMessage.success(`已为 ${selectedSearchUrls.value.length} 帖生成评论`)
    loadComments()
  } finally { commentGenerating.value = false }
}

function openCommentEdit(row: RedditComment) {
  commentEditForm.id = row.id
  commentEditForm.body = row.body
  commentEditDialog.value = true
}

async function saveCommentEdit() {
  commentSaving.value = true
  try {
    await updateRedditComment(commentEditForm.id, { body: commentEditForm.body })
    ElMessage.success('已保存')
    commentEditDialog.value = false
    loadComments()
  } finally { commentSaving.value = false }
}

async function approveComment(id: number) {
  await approveRedditComment(id)
  ElMessage.success('已批准')
  loadComments()
}

async function publishComment(id: number) {
  publishingCommentId.value = id
  try {
    const r = await publishRedditComment(id)
    ElMessage.success(r.status === 'posted' ? '评论发布成功' : `发布失败：${r.error_message}`)
    loadComments()
  } finally { publishingCommentId.value = null }
}

function statusLabel(s: string) {
  return { pending_review: '待审核', approved: '已批准', posted: '已发布', failed: '失败', rejected: '已拒绝' }[s] || s
}
function statusTag(s: string): any {
  return { pending_review: 'warning', approved: 'primary', posted: 'success', failed: 'danger' }[s] || 'info'
}
</script>

<style lang="scss" scoped>
.section-card { margin-bottom: 0; }
.card-title { font-weight: 600; }
.account-bar { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.list-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px; font-weight: 600; }
</style>
