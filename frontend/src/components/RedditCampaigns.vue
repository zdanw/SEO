<template>
  <div class="campaigns">
    <div class="toolbar">
      <div class="hint">
        在时间段内自动执行发帖 / 评论 / 点赞：按每日配额与活跃时段（北京时间）随机排期，同账号动作间隔 ≥25 分钟。
      </div>
      <div class="ops">
        <el-button :icon="Refresh" :loading="loading" @click="load">刷新</el-button>
        <el-button type="primary" :icon="Plus" @click="openCreate">新建 Campaign</el-button>
      </div>
    </div>

    <el-table v-loading="loading" :data="campaigns" size="small" stripe>
      <el-table-column prop="name" label="名称" min-width="140" show-overflow-tooltip />
      <el-table-column label="状态" width="92">
        <template #default="{ row }">
          <el-tag :type="statusTag(row.status)" size="small">{{ statusText(row.status) }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="日期区间" width="190">
        <template #default="{ row }">{{ row.start_date }} ~ {{ row.end_date }}</template>
      </el-table-column>
      <el-table-column label="活跃时段(北京时间)" width="130">
        <template #default="{ row }">{{ row.active_hour_start }}:00 - {{ row.active_hour_end }}:00</template>
      </el-table-column>
      <el-table-column label="模式" width="84">
        <template #default="{ row }">
          <el-tag :type="row.content_mode === 'auto' ? 'warning' : 'info'" size="small" effect="plain">
            {{ row.content_mode === 'auto' ? '自动发布' : '需审核' }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="今日进度 (发帖/评论/点赞)" min-width="200">
        <template #default="{ row }">
          <span :class="{ quota_done: row.today_posts >= row.daily_posts }">{{ row.today_posts }}/{{ row.daily_posts }}</span>
          <el-divider direction="vertical" />
          <span :class="{ quota_done: row.today_comments >= row.daily_comments }">{{ row.today_comments }}/{{ row.daily_comments }}</span>
          <el-divider direction="vertical" />
          <span :class="{ quota_done: row.today_upvotes >= row.daily_upvotes }">{{ row.today_upvotes }}/{{ row.daily_upvotes }}</span>
        </template>
      </el-table-column>
      <el-table-column label="社区" min-width="120" show-overflow-tooltip>
        <template #default="{ row }">{{ (row.subreddits || []).length ? row.subreddits.map(normSub).join(', ') : '按人设社区' }}</template>
      </el-table-column>
      <el-table-column label="操作" width="270" fixed="right">
        <template #default="{ row }">
          <el-button v-if="row.status === 'draft' || row.status === 'paused'" size="small" type="success" @click="start(row)">启动</el-button>
          <el-button v-if="row.status === 'running'" size="small" type="warning" @click="pause(row)">暂停</el-button>
          <el-button size="small" @click="openActions(row)">执行记录</el-button>
          <el-button size="small" @click="openEdit(row)">编辑</el-button>
          <el-button size="small" type="danger" plain @click="remove(row)">删除</el-button>
        </template>
      </el-table-column>
      <template #empty>暂无 Campaign，点击右上角新建</template>
    </el-table>

    <!-- 创建 / 编辑 -->
    <el-dialog v-model="dialogVisible" :title="editing ? '编辑 Campaign' : '新建 Campaign'" width="640px" destroy-on-close>
      <el-form :model="form" label-width="120px" size="small">
        <el-form-item label="名称" required>
          <el-input v-model="form.name" maxlength="120" placeholder="例如：9月养号-科技社区" />
        </el-form-item>
        <el-form-item label="日期区间" required>
          <el-date-picker
            v-model="dateRange"
            type="daterange"
            value-format="YYYY-MM-DD"
            start-placeholder="开始日期"
            end-placeholder="结束日期"
            style="width: 100%"
          />
        </el-form-item>
        <el-form-item label="活跃时段(北京时间)">
          <el-input-number v-model="form.active_hour_start" :min="0" :max="23" controls-position="right" />
          <span class="sep">至</span>
          <el-input-number v-model="form.active_hour_end" :min="0" :max="23" controls-position="right" />
          <span class="sep hint-inline">执行时间将随机分布在该时段内</span>
        </el-form-item>
        <el-form-item label="内容模式">
          <el-radio-group v-model="form.content_mode">
            <el-radio value="review">需审核（进待审队列）</el-radio>
            <el-radio value="auto">自动发布（AI 生成后定时发布）</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="每日配额">
          <div class="quota-row">
            <span>发帖</span><el-input-number v-model="form.daily_posts" :min="0" :max="20" controls-position="right" />
            <span>评论</span><el-input-number v-model="form.daily_comments" :min="0" :max="50" controls-position="right" />
            <span>点赞</span><el-input-number v-model="form.daily_upvotes" :min="0" :max="100" controls-position="right" />
          </div>
        </el-form-item>
        <el-form-item label="目标社区">
          <el-input v-model="subredditsText" placeholder="逗号分隔，如 technology, gadgets；留空则按账号人设社区" />
        </el-form-item>
        <el-form-item label="执行账号" required>
          <el-select v-model="form.account_ids" multiple filterable placeholder="选择本站 Reddit 账号" style="width: 100%">
            <el-option v-for="a in accounts" :key="a.id" :value="a.id" :label="`${a.account_name}${a.risk_status !== 'normal' ? '（风控预警）' : ''}`" :disabled="!a.is_active" />
          </el-select>
        </el-form-item>
        <el-form-item label="推广产品">
          <el-select v-model="form.product_id" clearable placeholder="可选；仅当发帖社区允许推广时生效" style="width: 100%">
            <el-option v-for="p in products" :key="p.id" :value="p.id" :label="`${p.brand_name ? p.brand_name + ' / ' : ''}${p.name}`" />
          </el-select>
        </el-form-item>
        <el-form-item label="自赞提热度">
          <el-switch v-model="form.self_upvote_enabled" />
          <template v-if="form.self_upvote_enabled">
            <span class="sep">发布后</span>
            <el-input-number v-model="form.self_upvote_delay_minutes" :min="10" :max="1440" :step="10" controls-position="right" />
            <span class="sep">分钟，由池内其他账号点赞</span>
          </template>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="save">保存</el-button>
      </template>
    </el-dialog>

    <!-- 执行记录 -->
    <el-drawer v-model="actionsVisible" :title="`执行记录 - ${activeCampaign?.name || ''}`" size="62%">
      <div class="filters">
        <el-select v-model="actionFilter.status" clearable placeholder="状态" size="small" style="width: 140px" @change="loadActions">
          <el-option v-for="(label, key) in ACTION_STATUS" :key="key" :label="label" :value="key" />
        </el-select>
        <el-select v-model="actionFilter.action_type" clearable placeholder="类型" size="small" style="width: 140px" @change="loadActions">
          <el-option v-for="(label, key) in ACTION_TYPES" :key="key" :label="label" :value="key" />
        </el-select>
        <el-button size="small" :icon="Refresh" @click="loadActions">刷新</el-button>
      </div>
      <el-table v-loading="actionsLoading" :data="actions" size="small" stripe>
        <el-table-column label="类型" width="110">
          <template #default="{ row }">{{ ACTION_TYPES[row.action_type as CampaignActionType] || row.action_type }}</template>
        </el-table-column>
        <el-table-column label="状态" width="92">
          <template #default="{ row }">
            <el-tag :type="ACTION_STATUS_TAG[row.status as CampaignActionStatus] || 'info'" size="small">{{ ACTION_STATUS[row.status as CampaignActionStatus] || row.status }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="account_name" label="账号" width="130" show-overflow-tooltip />
        <el-table-column label="计划时间" width="160">
          <template #default="{ row }">{{ fmtTime(row.planned_at) }}</template>
        </el-table-column>
        <el-table-column label="执行时间" width="160">
          <template #default="{ row }">{{ fmtTime(row.executed_at) }}</template>
        </el-table-column>
        <el-table-column label="目标 / 结果" min-width="220">
          <template #default="{ row }">
            <el-link v-if="row.target_post_url" :href="row.target_post_url" target="_blank" type="primary" class="target-link">
              {{ row.target_post_url }}
            </el-link>
            <div v-if="row.post_id" class="muted">帖子 #{{ row.post_id }}</div>
            <div v-if="row.comment_id" class="muted">评论 #{{ row.comment_id }}</div>
            <div v-if="row.error_message" class="err">{{ row.error_message }}</div>
            <div v-else-if="row.skip_reason" class="muted">跳过：{{ row.skip_reason }}</div>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="90" fixed="right">
          <template #default="{ row }">
            <el-button v-if="row.status === 'failed'" size="small" type="primary" link @click="retry(row)">重试</el-button>
          </template>
        </el-table-column>
        <template #empty>暂无执行记录，启动后由调度引擎生成</template>
      </el-table>
    </el-drawer>
  </div>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Plus, Refresh } from '@element-plus/icons-vue'
import {
  type RedditCampaign,
  type RedditCampaignAction,
  type CampaignActionStatus,
  type CampaignActionType,
  type CampaignStatus,
  type RedditCampaignPayload,
  createCampaign,
  deleteCampaign,
  listAccountProfiles,
  listCampaignActions,
  listCampaigns,
  listProducts,
  pauseCampaign,
  retryCampaignAction,
  startCampaign,
  updateCampaign,
  type RedditAccount,
  type RedditBrandProduct,
} from '../api/reddit'

const ACTION_TYPES: Record<CampaignActionType, string> = {
  post: '发帖',
  comment: '评论',
  upvote_related: '相关帖点赞',
  upvote_self: '自赞提热',
}
const ACTION_STATUS: Record<CampaignActionStatus, string> = {
  pending: '待执行',
  executing: '执行中',
  done: '完成',
  failed: '失败',
  skipped: '跳过',
}
const ACTION_STATUS_TAG: Record<CampaignActionStatus, string> = {
  pending: 'info',
  executing: 'warning',
  done: 'success',
  failed: 'danger',
  skipped: 'info',
}
const STATUS_TEXT: Record<CampaignStatus, string> = {
  draft: '草稿',
  running: '运行中',
  paused: '已暂停',
  ended: '已结束',
}
const STATUS_TAG: Record<CampaignStatus, string> = {
  draft: 'info',
  running: 'success',
  paused: 'warning',
  ended: 'info',
}

const campaigns = ref<RedditCampaign[]>([])
const accounts = ref<RedditAccount[]>([])
const products = ref<RedditBrandProduct[]>([])
const loading = ref(false)
const saving = ref(false)
const dialogVisible = ref(false)
const editing = ref<RedditCampaign | null>(null)
const dateRange = ref<[string, string] | null>(null)
const subredditsText = ref('')

const form = reactive<RedditCampaignPayload>({
  name: '',
  start_date: '',
  end_date: '',
  active_hour_start: 8,
  active_hour_end: 22,
  content_mode: 'review',
  daily_posts: 0,
  daily_comments: 0,
  daily_upvotes: 0,
  self_upvote_enabled: false,
  self_upvote_delay_minutes: 240,
  subreddits: [],
  account_ids: [],
  product_id: null,
})

const actionsVisible = ref(false)
const actionsLoading = ref(false)
const actions = ref<RedditCampaignAction[]>([])
const activeCampaign = ref<RedditCampaign | null>(null)
const actionFilter = reactive<{ status?: string; action_type?: string }>({})

function normSub(s: string) {
  return s.replace(/^\/?r\//i, '').trim()
}
function statusText(s: CampaignStatus) {
  return STATUS_TEXT[s] || s
}
function statusTag(s: CampaignStatus) {
  return STATUS_TAG[s] || 'info'
}
function fmtTime(v?: string | null) {
  if (!v) return '-'
  // 后端全链路北京时间：timestamptz 读回带 +08:00；无时区标识的 naive 串按北京时间补偏移
  const s = /([zZ]|[+-]\d{2}:?\d{2})$/.test(v) ? v : `${v}+08:00`
  return new Date(s).toLocaleString()
}

async function load() {
  loading.value = true
  try {
    campaigns.value = await listCampaigns()
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || '加载 Campaign 失败')
  } finally {
    loading.value = false
  }
}

async function loadMeta() {
  try {
    const [acc, prod] = await Promise.all([listAccountProfiles(), listProducts()])
    accounts.value = acc
    products.value = prod
  } catch {
    /* 元数据加载失败不阻断列表 */
  }
}

function resetForm() {
  editing.value = null
  dateRange.value = null
  subredditsText.value = ''
  Object.assign(form, {
    name: '',
    start_date: '',
    end_date: '',
    active_hour_start: 8,
    active_hour_end: 22,
    content_mode: 'review',
    daily_posts: 0,
    daily_comments: 0,
    daily_upvotes: 0,
    self_upvote_enabled: false,
    self_upvote_delay_minutes: 240,
    subreddits: [],
    account_ids: [],
    product_id: null,
  })
}

function openCreate() {
  resetForm()
  dialogVisible.value = true
}

function openEdit(row: RedditCampaign) {
  editing.value = row
  dateRange.value = [row.start_date, row.end_date]
  subredditsText.value = (row.subreddits || []).join(', ')
  Object.assign(form, {
    name: row.name,
    start_date: row.start_date,
    end_date: row.end_date,
    active_hour_start: row.active_hour_start,
    active_hour_end: row.active_hour_end,
    content_mode: row.content_mode,
    daily_posts: row.daily_posts,
    daily_comments: row.daily_comments,
    daily_upvotes: row.daily_upvotes,
    self_upvote_enabled: row.self_upvote_enabled,
    self_upvote_delay_minutes: row.self_upvote_delay_minutes,
    subreddits: row.subreddits || [],
    account_ids: row.account_ids || [],
    product_id: row.product_id ?? null,
  })
  dialogVisible.value = true
}

async function save() {
  if (!dateRange.value || dateRange.value.length !== 2) {
    ElMessage.warning('请选择日期区间')
    return
  }
  if (!form.name.trim()) {
    ElMessage.warning('请填写名称')
    return
  }
  if (!form.account_ids.length) {
    ElMessage.warning('请选择执行账号')
    return
  }
  form.start_date = dateRange.value[0]
  form.end_date = dateRange.value[1]
  form.subreddits = subredditsText.value
    .split(/[,，\s]+/)
    .map(normSub)
    .filter(Boolean)
  saving.value = true
  try {
    if (editing.value) {
      await updateCampaign(editing.value.id, form)
      ElMessage.success('已更新')
    } else {
      await createCampaign(form)
      ElMessage.success('已创建，启动后生效')
    }
    dialogVisible.value = false
    await load()
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || '保存失败')
  } finally {
    saving.value = false
  }
}

async function start(row: RedditCampaign) {
  try {
    await startCampaign(row.id)
    ElMessage.success('已启动')
    await load()
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || '启动失败')
  }
}

async function pause(row: RedditCampaign) {
  try {
    await pauseCampaign(row.id)
    ElMessage.success('已暂停')
    await load()
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || '暂停失败')
  }
}

async function remove(row: RedditCampaign) {
  try {
    await ElMessageBox.confirm(`删除 Campaign「${row.name}」？其执行记录将一并删除。`, '确认', { type: 'warning' })
  } catch {
    return
  }
  try {
    await deleteCampaign(row.id)
    ElMessage.success('已删除')
    await load()
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || '删除失败')
  }
}

async function loadActions() {
  if (!activeCampaign.value) return
  actionsLoading.value = true
  try {
    actions.value = await listCampaignActions(activeCampaign.value.id, {
      status: actionFilter.status,
      action_type: actionFilter.action_type,
      limit: 200,
    })
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || '加载执行记录失败')
  } finally {
    actionsLoading.value = false
  }
}

function openActions(row: RedditCampaign) {
  activeCampaign.value = row
  actionFilter.status = undefined
  actionFilter.action_type = undefined
  actionsVisible.value = true
  loadActions()
}

async function retry(row: RedditCampaignAction) {
  try {
    await retryCampaignAction(row.id)
    ElMessage.success('已重新入队')
    await loadActions()
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || '重试失败')
  }
}

onMounted(() => {
  load()
  loadMeta()
})
</script>

<style scoped>
.campaigns {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.toolbar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
}
.hint {
  color: var(--el-text-color-secondary);
  font-size: 12px;
}
.ops {
  display: flex;
  gap: 8px;
}
.quota_done {
  color: var(--el-color-success);
  font-weight: 600;
}
.quota-row {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
}
.quota-row .el-input-number {
  width: 100px;
}
.sep {
  margin: 0 6px;
  color: var(--el-text-color-secondary);
}
.hint-inline {
  font-size: 12px;
}
.filters {
  display: flex;
  gap: 8px;
  margin-bottom: 10px;
}
.target-link {
  font-size: 12px;
  word-break: break-all;
}
.muted {
  color: var(--el-text-color-secondary);
  font-size: 12px;
}
.err {
  color: var(--el-color-danger);
  font-size: 12px;
  word-break: break-all;
}
</style>
