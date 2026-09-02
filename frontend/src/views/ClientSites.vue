<template>
  <div class="page-container">
    <div class="page-toolbar">
      <el-button type="primary" :disabled="siteStore.isReadOnly" @click="openCreate">新建客户站点</el-button>
      <el-button @click="loadSites">刷新</el-button>
    </div>

    <el-row :gutter="16">
      <el-col v-for="site in siteStore.sites" :key="site.id" :xs="24" :md="12" :lg="8">
        <el-card shadow="hover" class="site-card" :class="{ active: site.id === siteStore.currentSiteId }">
          <div class="site-header">
            <div>
              <h3>{{ site.name }}</h3>
              <p class="domain">{{ site.domain }}</p>
            </div>
            <el-tag :type="site.status === 'active' ? 'success' : 'info'" size="small">{{ site.status }}</el-tag>
          </div>
          <div class="site-meta">
            <span>CMS: {{ site.cms_type }}</span>
            <span>角色: {{ site.role || 'admin' }}</span>
          </div>
          <div class="site-actions">
            <el-button size="small" @click="selectSite(site.id)">切换到此站点</el-button>
            <el-button size="small" :disabled="siteStore.isReadOnly" @click="openEdit(site)">编辑</el-button>
            <el-button size="small" type="danger" :disabled="site.role !== 'admin'" @click="removeSite(site)">
              删除
            </el-button>
          </div>
        </el-card>
      </el-col>
    </el-row>

    <el-empty v-if="!siteStore.sites.length && !loading" description="暂无客户站点，请先创建" />

    <el-dialog v-model="dialogVisible" :title="editing ? '编辑客户站点' : '新建客户站点'" width="520px">
      <el-form label-position="top">
        <el-form-item label="客户名称" required>
          <el-input v-model="form.name" placeholder="如：ABC 科技官网" />
        </el-form-item>
        <el-form-item label="主域名" required>
          <el-input v-model="form.domain" placeholder="example.com" />
        </el-form-item>
        <el-form-item label="行业">
          <el-input v-model="form.industry" />
        </el-form-item>
        <el-form-item label="CMS 类型">
          <el-select v-model="form.cms_type" style="width: 100%">
            <el-option label="无" value="none" />
            <el-option label="WordPress" value="wordpress" />
            <el-option label="Shopify" value="shopify" />
            <el-option label="自定义" value="custom" />
          </el-select>
        </el-form-item>
        <el-form-item v-if="form.cms_type === 'wordpress'" label="WordPress API URL">
          <el-input v-model="form.cms_api_url" placeholder="https://example.com" />
        </el-form-item>
        <el-form-item v-if="form.cms_type === 'wordpress'" label="API 凭证 (user:app_password)">
          <el-input v-model="form.cms_api_key" type="password" show-password />
        </el-form-item>
        <el-form-item label="Sitemap URL">
          <el-input v-model="form.sitemap_url" placeholder="https://example.com/sitemap.xml" />
        </el-form-item>
        <el-form-item label="备注">
          <el-input v-model="form.notes" type="textarea" :rows="2" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="saveSite">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useSiteStore } from '@/stores/site'
import {
  createSite,
  updateSite,
  deleteSite,
  type ClientSite,
  type ClientSiteCreate,
} from '@/api/sites'

const siteStore = useSiteStore()
const loading = ref(false)
const saving = ref(false)
const dialogVisible = ref(false)
const editing = ref<ClientSite | null>(null)

const form = reactive<ClientSiteCreate>({
  name: '',
  domain: '',
  industry: '',
  cms_type: 'none',
  cms_api_url: '',
  cms_api_key: '',
  sitemap_url: '',
  notes: '',
})

onMounted(loadSites)

async function loadSites() {
  loading.value = true
  try {
    await siteStore.fetchSites()
  } finally {
    loading.value = false
  }
}

function resetForm() {
  form.name = ''
  form.domain = ''
  form.industry = ''
  form.cms_type = 'none'
  form.cms_api_url = ''
  form.cms_api_key = ''
  form.sitemap_url = ''
  form.notes = ''
}

function openCreate() {
  editing.value = null
  resetForm()
  dialogVisible.value = true
}

function openEdit(site: ClientSite) {
  editing.value = site
  form.name = site.name
  form.domain = site.domain
  form.industry = site.industry || ''
  form.cms_type = site.cms_type
  form.cms_api_url = site.cms_api_url || ''
  form.sitemap_url = site.sitemap_url || ''
  form.notes = site.notes || ''
  dialogVisible.value = true
}

async function saveSite() {
  if (!form.name || !form.domain) {
    ElMessage.warning('请填写客户名称和域名')
    return
  }
  saving.value = true
  try {
    if (editing.value) {
      await updateSite(editing.value.id, { ...form })
      ElMessage.success('站点已更新')
    } else {
      const site = await createSite({ ...form })
      siteStore.setCurrentSite(site.id)
      ElMessage.success('站点已创建')
    }
    dialogVisible.value = false
    await loadSites()
  } finally {
    saving.value = false
  }
}

function selectSite(id: number) {
  siteStore.setCurrentSite(id)
  ElMessage.success('已切换客户站点')
}

async function removeSite(site: ClientSite) {
  await ElMessageBox.confirm(`确定删除客户站点「${site.name}」？`, '确认删除', { type: 'warning' })
  await deleteSite(site.id)
  ElMessage.success('已删除')
  await loadSites()
}
</script>

<style scoped lang="scss">
.site-card {
  margin-bottom: 16px;

  &.active {
    border-color: #409eff;
  }

  .site-header {
    display: flex;
    justify-content: space-between;
    align-items: flex-start;

    h3 {
      margin: 0 0 4px;
      font-size: 16px;
    }

    .domain {
      margin: 0;
      color: #909399;
      font-size: 13px;
    }
  }

  .site-meta {
    display: flex;
    gap: 16px;
    margin: 12px 0;
    font-size: 13px;
    color: #606266;
  }

  .site-actions {
    display: flex;
    gap: 8px;
    flex-wrap: wrap;
  }
}
</style>
