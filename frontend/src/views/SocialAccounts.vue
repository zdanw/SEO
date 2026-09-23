<template>
  <div class="page-container">
    <el-card shadow="never" class="section-card">
      <template #header>
        <div class="card-title-row">
          <div>
            <span class="card-title">Zernio Key</span>
            <p class="muted" style="margin: 4px 0 0">当前站点专用；用于连接 Zernio 并同步 Reddit 账号，可配置多把 Key。</p>
          </div>
          <el-button type="primary" @click="keyDialog = true">添加 Key</el-button>
        </div>
      </template>
      <div class="account-bar" style="margin-bottom: 12px">
        <el-tag v-if="status?.configured" type="success" size="small">
          已配置 {{ status.zernio_key_count || 0 }} 把 Key
        </el-tag>
        <el-tag v-else type="warning" size="small">未配置（Mock 模式）</el-tag>
      </div>
      <el-table :data="zernioKeys" v-loading="keysLoading" size="small" stripe empty-text="暂无 Key，请点击右上角添加">
        <el-table-column prop="label" label="备注" min-width="140" />
        <el-table-column prop="api_key_masked" label="API Key" min-width="160" />
        <el-table-column label="启用" width="90">
          <template #default="{ row }">
            <el-switch :model-value="row.is_enabled" @change="(v: string | number | boolean) => toggleKey(row, Boolean(v))" />
          </template>
        </el-table-column>
        <el-table-column label="操作" width="90">
          <template #default="{ row }">
            <el-button type="danger" link @click="removeKey(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <el-card shadow="never" class="section-card" style="margin-top: 16px">
      <template #header>
        <div class="card-title-row">
          <div>
            <span class="card-title">Reddit 账号</span>
            <p class="muted" style="margin: 4px 0 0">在 Zernio 绑定账号后同步到本系统；社交分发时选择发布账号。</p>
          </div>
          <div>
            <el-button @click="openZernio">在 Zernio 管理账号</el-button>
            <el-button type="primary" :loading="syncing" @click="handleSync">同步账号</el-button>
            <el-button @click="refresh">刷新</el-button>
          </div>
        </div>
      </template>
      <div class="account-bar" style="margin-bottom: 12px">
        <el-tag v-if="status?.connected" type="success" size="small">已同步</el-tag>
        <el-tag v-else type="info" size="small">未同步</el-tag>
        <span class="muted">共 {{ status?.accounts?.length || 0 }} 个账号</span>
      </div>
      <el-table :data="status?.accounts || []" v-loading="statusLoading" size="small" stripe empty-text="暂无账号，请先配置 Key 并同步">
        <el-table-column prop="account_name" label="账号" min-width="160" />
        <el-table-column label="Key" min-width="140">
          <template #default="{ row }">{{ row.zernio_key_label || '—' }}</template>
        </el-table-column>
        <el-table-column label="阶段" width="120">
          <template #default="{ row }">{{ row.stage || '—' }}</template>
        </el-table-column>
        <el-table-column label="角色" width="100">
          <template #default="{ row }">{{ row.role || '—' }}</template>
        </el-table-column>
        <el-table-column label="Karma" width="90">
          <template #default="{ row }">{{ row.karma ?? '—' }}</template>
        </el-table-column>
      </el-table>
      <p class="muted" style="margin-top: 12px">
        账号角色与养号阶段请到
        <router-link to="/social?tab=accounts">社交分发 · 账号矩阵</router-link>
        配置。
      </p>
    </el-card>

    <el-dialog v-model="keyDialog" title="添加 Zernio Key" width="480px">
      <el-form label-position="top">
        <el-form-item label="备注名">
          <el-input v-model="keyForm.label" placeholder="Zernio-免费-1" />
        </el-form-item>
        <el-form-item label="API Key">
          <el-input v-model="keyForm.api_key" placeholder="sk_..." show-password />
        </el-form-item>
        <el-form-item label="Profile ID（可选）">
          <el-input v-model="keyForm.profile_id" placeholder="不填则由 Zernio 自动处理" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="keyDialog = false">取消</el-button>
        <el-button type="primary" :loading="keySaving" @click="submitKey">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  getRedditStatus, syncRedditAccounts,
  listZernioKeys, createZernioKey, updateZernioKey, deleteZernioKey,
  type RedditStatus, type ZernioKey,
} from '@/api/reddit'
import { useSiteReload } from '@/composables/useSiteReload'

const status = ref<RedditStatus | null>(null)
const statusLoading = ref(false)
const zernioKeys = ref<ZernioKey[]>([])
const keysLoading = ref(false)
const syncing = ref(false)
const keyDialog = ref(false)
const keySaving = ref(false)
const keyForm = reactive({ label: '', api_key: '', profile_id: '' })

function openZernio() {
  window.open('https://zernio.com', '_blank', 'noopener')
}

async function loadKeys() {
  keysLoading.value = true
  try {
    zernioKeys.value = await listZernioKeys()
  } catch {
    zernioKeys.value = []
  } finally {
    keysLoading.value = false
  }
}

async function loadStatus() {
  statusLoading.value = true
  try {
    status.value = await getRedditStatus()
  } finally {
    statusLoading.value = false
  }
}

async function refresh() {
  await Promise.all([loadKeys(), loadStatus()])
}

async function handleSync() {
  syncing.value = true
  try {
    status.value = await syncRedditAccounts()
    if (status.value.sync_errors?.length) {
      ElMessage.warning(status.value.sync_errors.join('；'))
    } else {
      ElMessage.success('账号已同步')
    }
    await loadKeys()
  } finally {
    syncing.value = false
  }
}

async function submitKey() {
  if (!keyForm.label.trim() || !keyForm.api_key.trim()) {
    ElMessage.warning('请填写备注和 API Key')
    return
  }
  keySaving.value = true
  try {
    await createZernioKey({
      label: keyForm.label.trim(),
      api_key: keyForm.api_key.trim(),
      profile_id: keyForm.profile_id.trim() || undefined,
    })
    ElMessage.success('已添加 Key')
    keyDialog.value = false
    keyForm.label = ''
    keyForm.api_key = ''
    keyForm.profile_id = ''
    await refresh()
  } finally {
    keySaving.value = false
  }
}

async function toggleKey(row: ZernioKey, enabled: boolean) {
  await updateZernioKey(row.id, { is_enabled: enabled })
  await refresh()
}

async function removeKey(row: ZernioKey) {
  try {
    await ElMessageBox.confirm(`删除 Key「${row.label}」？已绑定账号发帖将失败，需重新同步。`, '删除确认', {
      type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消',
    })
  } catch {
    return
  }
  await deleteZernioKey(row.id)
  ElMessage.success('已删除')
  await refresh()
}

onMounted(refresh)
useSiteReload(refresh)
</script>

<style lang="scss" scoped>
.page-container {
  max-width: 1100px;
}
.card-title-row {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
}
.card-title {
  font-weight: 600;
}
.muted {
  color: var(--el-text-color-secondary);
  font-size: 13px;
}
.account-bar {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}
</style>
