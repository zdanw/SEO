<template>
  <div class="page-container">
    <el-card shadow="never" class="section-card">
      <template #header>
        <div class="card-title-row">
          <div>
            <span class="card-title">品牌</span>
            <p class="muted" style="margin: 4px 0 0">维护品牌资料；产品请到「产品」页管理。</p>
          </div>
          <el-button type="primary" @click="openBrandEdit()">新增品牌</el-button>
        </div>
      </template>
      <el-table :data="brands" v-loading="loading" size="small" stripe empty-text="暂无品牌，请先新增">
        <el-table-column prop="name" label="品牌" min-width="180" />
        <el-table-column label="产品数" width="100">
          <template #default="{ row }">
            {{ (row.products || []).length }}
          </template>
        </el-table-column>
        <el-table-column label="启用" width="90">
          <template #default="{ row }">
            <el-switch :model-value="row.is_active" @change="(v: string | number | boolean) => toggleBrand(row, Boolean(v))" />
          </template>
        </el-table-column>
        <el-table-column label="操作" width="140" fixed="right">
          <template #default="{ row }">
            <el-button size="small" @click="openBrandEdit(row)">编辑</el-button>
            <el-button size="small" type="danger" @click="removeBrand(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <el-dialog v-model="brandDialog" :title="brandForm.id ? '编辑品牌' : '新增品牌'" width="420px">
      <el-form label-position="top">
        <el-form-item label="品牌名">
          <el-input v-model="brandForm.name" placeholder="Bebcare" />
        </el-form-item>
        <el-form-item label="启用">
          <el-switch v-model="brandForm.is_active" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="brandDialog = false">取消</el-button>
        <el-button type="primary" :loading="brandSaving" @click="saveBrand">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  listBrands, createBrand, updateBrand, deleteBrand,
  type RedditBrand,
} from '@/api/reddit'

const brands = ref<RedditBrand[]>([])
const loading = ref(false)
const brandDialog = ref(false)
const brandSaving = ref(false)
const brandForm = reactive({ id: 0, name: '', is_active: true })

async function loadBrands() {
  loading.value = true
  try {
    brands.value = await listBrands()
  } finally {
    loading.value = false
  }
}

function openBrandEdit(row?: RedditBrand) {
  if (row) {
    brandForm.id = row.id
    brandForm.name = row.name
    brandForm.is_active = row.is_active
  } else {
    brandForm.id = 0
    brandForm.name = ''
    brandForm.is_active = true
  }
  brandDialog.value = true
}

async function saveBrand() {
  if (!brandForm.name.trim()) {
    ElMessage.warning('请填写品牌名')
    return
  }
  brandSaving.value = true
  try {
    if (brandForm.id) {
      await updateBrand(brandForm.id, { name: brandForm.name.trim(), is_active: brandForm.is_active })
    } else {
      await createBrand({ name: brandForm.name.trim(), is_active: brandForm.is_active })
    }
    ElMessage.success('品牌已保存')
    brandDialog.value = false
    await loadBrands()
  } finally {
    brandSaving.value = false
  }
}

async function toggleBrand(row: RedditBrand, enabled: boolean) {
  await updateBrand(row.id, { is_active: enabled })
  await loadBrands()
}

async function removeBrand(row: RedditBrand) {
  try {
    await ElMessageBox.confirm(`删除品牌「${row.name}」及其产品？`, '删除确认', {
      type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消',
    })
  } catch {
    return
  }
  await deleteBrand(row.id)
  ElMessage.success('已删除')
  await loadBrands()
}

onMounted(loadBrands)
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
