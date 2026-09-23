<template>
  <div class="page-container">
    <el-card shadow="never" class="section-card">
      <template #header>
        <div class="card-title-row">
          <div>
            <span class="card-title">客户站点</span>
            <p class="muted" style="margin: 4px 0 0">
              管理代运营站点；顶栏可切换当前站。未选择时使用默认站（刷新后亦回默认）。
            </p>
          </div>
          <el-button type="primary" @click="openEdit()">新增站点</el-button>
        </div>
      </template>
      <el-table :data="sites" v-loading="loading" size="small" stripe empty-text="暂无站点">
        <el-table-column prop="name" label="名称" min-width="140" />
        <el-table-column prop="domain" label="域名" min-width="160" />
        <el-table-column label="状态" width="100">
          <template #default="{ row }">
            <el-tag :type="statusType(row.status)" size="small">{{ statusLabel(row.status) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="industry" label="行业" min-width="120" show-overflow-tooltip />
        <el-table-column prop="notes" label="备注" min-width="160" show-overflow-tooltip />
        <el-table-column label="角色" width="100">
          <template #default="{ row }">{{ row.role || '—' }}</template>
        </el-table-column>
        <el-table-column label="操作" width="160" fixed="right">
          <template #default="{ row }">
            <el-button size="small" @click="openEdit(row)">编辑</el-button>
            <el-button size="small" type="danger" @click="remove(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <el-dialog v-model="dialogVisible" :title="form.id ? '编辑站点' : '新增站点'" width="480px">
      <el-form label-position="top">
        <el-form-item label="名称" required>
          <el-input v-model="form.name" placeholder="客户品牌站" />
        </el-form-item>
        <el-form-item label="域名" required>
          <el-input v-model="form.domain" placeholder="example.com" />
        </el-form-item>
        <el-form-item v-if="form.id" label="状态">
          <el-select v-model="form.status" style="width: 100%">
            <el-option label="启用" value="active" />
            <el-option label="暂停" value="paused" />
            <el-option label="归档" value="archived" />
          </el-select>
        </el-form-item>
        <el-form-item label="行业">
          <el-input v-model="form.industry" placeholder="可选" />
        </el-form-item>
        <el-form-item label="备注">
          <el-input v-model="form.notes" type="textarea" :rows="2" placeholder="可选" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="save">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { storeToRefs } from 'pinia'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  createSite,
  updateSite,
  deleteSite,
  type ClientSite,
  type SiteStatus,
} from '@/api/sites'
import { useSiteStore } from '@/stores/site'

const siteStore = useSiteStore()
const { sites, loading, currentSiteId } = storeToRefs(siteStore)

const dialogVisible = ref(false)
const saving = ref(false)
const form = reactive({
  id: 0,
  name: '',
  domain: '',
  status: 'active' as SiteStatus,
  industry: '',
  notes: '',
})

function statusLabel(s: SiteStatus) {
  const map: Record<SiteStatus, string> = {
    active: '启用',
    paused: '暂停',
    archived: '归档',
  }
  return map[s] || s
}

function statusType(s: SiteStatus) {
  if (s === 'active') return 'success'
  if (s === 'paused') return 'warning'
  return 'info'
}

function openEdit(row?: ClientSite) {
  if (row) {
    form.id = row.id
    form.name = row.name
    form.domain = row.domain
    form.status = row.status
    form.industry = row.industry || ''
    form.notes = row.notes || ''
  } else {
    form.id = 0
    form.name = ''
    form.domain = ''
    form.status = 'active'
    form.industry = ''
    form.notes = ''
  }
  dialogVisible.value = true
}

async function save() {
  if (!form.name.trim() || !form.domain.trim()) {
    ElMessage.warning('请填写名称和域名')
    return
  }
  saving.value = true
  try {
    if (form.id) {
      await updateSite(form.id, {
        name: form.name.trim(),
        domain: form.domain.trim(),
        status: form.status,
        industry: form.industry.trim() || null,
        notes: form.notes.trim() || null,
      })
    } else {
      await createSite({
        name: form.name.trim(),
        domain: form.domain.trim(),
        industry: form.industry.trim() || null,
        notes: form.notes.trim() || null,
      })
    }
    ElMessage.success('站点已保存')
    dialogVisible.value = false
    await siteStore.loadSites()
  } finally {
    saving.value = false
  }
}

async function remove(row: ClientSite) {
  try {
    await ElMessageBox.confirm(
      `删除站点「${row.name}」将清除其关键词、社交账号与 Reddit 队列等全部关联数据，且不可恢复。确认删除？`,
      '删除确认',
      { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  await deleteSite(row.id)
  if (currentSiteId.value === row.id) {
    siteStore.setCurrentSite(null)
  }
  ElMessage.success('已删除')
  await siteStore.loadSites()
}

onMounted(() => {
  void siteStore.loadSites()
})
</script>

<style lang="scss" scoped>
.page-container {
  max-width: 1200px;
}
.card-title-row {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
}
.card-title {
  font-weight: 600;
}
.muted {
  color: var(--el-text-color-secondary);
  font-size: 13px;
}
</style>
