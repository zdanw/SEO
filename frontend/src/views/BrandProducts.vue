<template>
  <div class="page-container">
    <el-card shadow="never" class="section-card">
      <template #header>
        <div class="card-title-row">
          <div>
            <span class="card-title">品牌 / 产品库</span>
            <p class="muted" style="margin: 4px 0 0">用于约 10% 产品向 Reddit 评论；每个产品可绑定多个产品社区（多对多），智能发现只进入已绑定社区。</p>
          </div>
          <el-button type="primary" @click="openBrandEdit()">新增品牌</el-button>
        </div>
      </template>
      <el-table :data="brands" v-loading="loading" size="small" stripe empty-text="暂无品牌，请先新增">
        <el-table-column prop="name" label="品牌" min-width="140" />
        <el-table-column label="产品" min-width="320">
          <template #default="{ row }">
            <el-tag
              v-for="p in row.products"
              :key="p.id"
              size="small"
              style="margin: 2px 4px 2px 0; cursor: pointer"
              :type="p.is_active ? 'warning' : 'info'"
              closable
              @close="removeProduct(p)"
              @click="openProductEdit(row, p)"
            >{{ p.name }}{{ p.category ? ` · ${p.category}` : '' }}{{ (p.community_names || []).length ? ` · ${p.community_names.length}社区` : '' }}</el-tag>
            <el-button size="small" link type="primary" @click="openProductEdit(row)">+ 产品</el-button>
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

    <el-dialog v-model="productDialog" :title="productForm.id ? '编辑产品' : '新增产品'" width="560px">
      <el-form label-position="top">
        <el-form-item label="产品名">
          <el-input v-model="productForm.name" placeholder="baby monitor" />
        </el-form-item>
        <el-form-item label="品类">
          <el-input v-model="productForm.category" placeholder="low EMF baby monitor" />
        </el-form-item>
        <el-form-item label="可说卖点（逗号分隔）">
          <el-input v-model="productTalkingText" placeholder="no wifi, analog, low EMF" />
        </el-form-item>
        <el-form-item label="绑定产品社区（可多选；同一社区可绑多个产品）">
          <el-select
            v-model="productForm.community_ids"
            multiple
            filterable
            clearable
            placeholder="选择产品社区"
            style="width: 100%"
          >
            <el-option
              v-for="c in promoCommunities"
              :key="c.id"
              :label="`r/${c.name}`"
              :value="c.id"
            />
          </el-select>
          <p class="muted" style="margin: 6px 0 0">仅列出社区库中 purpose=产品 的社区。未绑定则智能发现不会抽产品槽。</p>
        </el-form-item>
        <el-form-item label="启用">
          <el-switch v-model="productForm.is_active" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="productDialog = false">取消</el-button>
        <el-button type="primary" :loading="productSaving" @click="saveProduct">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  listBrands, createBrand, updateBrand, deleteBrand,
  createBrandProduct, updateBrandProduct, deleteBrandProduct,
  listCommunities,
  type RedditBrand, type RedditBrandProduct, type RedditCommunity,
} from '@/api/reddit'

const brands = ref<RedditBrand[]>([])
const promoCommunities = ref<RedditCommunity[]>([])
const loading = ref(false)
const brandDialog = ref(false)
const brandSaving = ref(false)
const brandForm = reactive({ id: 0, name: '', is_active: true })
const productDialog = ref(false)
const productSaving = ref(false)
const productTalkingText = ref('')
const productForm = reactive({
  id: 0, brand_id: 0, name: '', category: '', is_active: true, community_ids: [] as number[],
})

function splitComma(text: string) {
  return text.split(/[,，]/).map((s) => s.trim()).filter(Boolean)
}

async function loadBrands() {
  loading.value = true
  try {
    brands.value = await listBrands()
  } finally {
    loading.value = false
  }
}

async function loadPromoCommunities() {
  const rows = await listCommunities()
  promoCommunities.value = (rows || []).filter((c) => c.purpose === 'promo' && c.is_active)
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

function openProductEdit(brand: RedditBrand, row?: RedditBrandProduct) {
  productForm.brand_id = brand.id
  if (row) {
    productForm.id = row.id
    productForm.name = row.name
    productForm.category = row.category || ''
    productForm.is_active = row.is_active
    productForm.community_ids = [...(row.community_ids || [])]
    productTalkingText.value = (row.talking_points || []).join(', ')
  } else {
    productForm.id = 0
    productForm.name = ''
    productForm.category = ''
    productForm.is_active = true
    productForm.community_ids = []
    productTalkingText.value = ''
  }
  productDialog.value = true
}

async function saveProduct() {
  if (!productForm.name.trim()) {
    ElMessage.warning('请填写产品名')
    return
  }
  productSaving.value = true
  try {
    const payload = {
      name: productForm.name.trim(),
      category: productForm.category.trim(),
      talking_points: splitComma(productTalkingText.value),
      is_active: productForm.is_active,
      community_ids: productForm.community_ids,
    }
    if (productForm.id) {
      await updateBrandProduct(productForm.id, payload)
    } else {
      await createBrandProduct(productForm.brand_id, payload)
    }
    ElMessage.success('产品已保存')
    productDialog.value = false
    await loadBrands()
  } finally {
    productSaving.value = false
  }
}

async function removeProduct(row: RedditBrandProduct) {
  try {
    await ElMessageBox.confirm(`删除产品「${row.name}」？`, '删除确认', {
      type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消',
    })
  } catch {
    return
  }
  await deleteBrandProduct(row.id)
  ElMessage.success('已删除')
  await loadBrands()
}

onMounted(async () => {
  await Promise.all([loadBrands(), loadPromoCommunities()])
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
