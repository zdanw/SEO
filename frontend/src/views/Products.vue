<template>
  <div class="page-container">
    <el-card shadow="never" class="section-card">
      <template #header>
        <div class="card-title-row">
          <div>
            <span class="card-title">产品</span>
            <p class="muted" style="margin: 4px 0 0">每个产品可绑定关键词与产品社区；智能发现用关键词在绑定社区中搜索。</p>
          </div>
          <el-button type="primary" :disabled="!brands.length" @click="openProductEdit()">新增产品</el-button>
        </div>
      </template>
      <el-alert
        v-if="!loading && !brands.length"
        type="info"
        :closable="false"
        show-icon
        title="请先在「品牌」页新增品牌，再添加产品。"
        style="margin-bottom: 12px"
      />
      <el-table :data="products" v-loading="loading" size="small" stripe empty-text="暂无产品">
        <el-table-column prop="brand_name" label="品牌" min-width="120" />
        <el-table-column prop="name" label="产品" min-width="160" />
        <el-table-column prop="category" label="品类" min-width="140" show-overflow-tooltip />
        <el-table-column label="关键词" width="90">
          <template #default="{ row }">
            {{ (row.keywords || []).length }}
          </template>
        </el-table-column>
        <el-table-column label="社区" width="80">
          <template #default="{ row }">
            {{ (row.community_names || []).length }}
          </template>
        </el-table-column>
        <el-table-column label="启用" width="90">
          <template #default="{ row }">
            <el-switch :model-value="row.is_active" @change="(v: string | number | boolean) => toggleProduct(row, Boolean(v))" />
          </template>
        </el-table-column>
        <el-table-column label="操作" width="140" fixed="right">
          <template #default="{ row }">
            <el-button size="small" @click="openProductEdit(row)">编辑</el-button>
            <el-button size="small" type="danger" @click="removeProduct(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <el-dialog v-model="productDialog" :title="productForm.id ? '编辑产品' : '新增产品'" width="560px">
      <el-form label-position="top">
        <el-form-item label="所属品牌">
          <el-select
            v-model="productForm.brand_id"
            filterable
            placeholder="选择品牌"
            style="width: 100%"
            :disabled="!!productForm.id"
          >
            <el-option v-for="b in brands" :key="b.id" :label="b.name" :value="b.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="产品名">
          <el-input v-model="productForm.name" placeholder="baby monitor" />
        </el-form-item>
        <el-form-item label="品类">
          <el-input v-model="productForm.category" placeholder="low EMF baby monitor" />
        </el-form-item>
        <el-form-item label="可说卖点（逗号分隔）">
          <el-input v-model="productTalkingText" placeholder="no wifi, analog, low EMF" />
        </el-form-item>
        <el-form-item label="产品信息">
          <el-input
            v-model="productForm.description"
            type="textarea"
            :rows="4"
            maxlength="2000"
            show-word-limit
            placeholder="产品是什么、适用场景、核心差异等，供 AI 生成帖/评时参考"
          />
          <p class="muted" style="margin: 6px 0 0">可选。写入 LLM 上下文，帮助生成更贴合产品的内容。</p>
        </el-form-item>
        <el-form-item label="关键词（逗号分隔）">
          <el-input v-model="productKeywordsText" placeholder="low EMF baby monitor, non-wifi baby monitor" />
          <p class="muted" style="margin: 6px 0 0">智能发现在绑定社区中用这些词搜索；未填则不会做产品向搜索。</p>
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
  listBrands, listProducts,
  createBrandProduct, updateBrandProduct, deleteBrandProduct,
  listCommunities,
  type RedditBrand, type RedditBrandProduct, type RedditCommunity,
} from '@/api/reddit'

const brands = ref<RedditBrand[]>([])
const products = ref<RedditBrandProduct[]>([])
const promoCommunities = ref<RedditCommunity[]>([])
const loading = ref(false)
const productDialog = ref(false)
const productSaving = ref(false)
const productTalkingText = ref('')
const productKeywordsText = ref('')
const productForm = reactive({
  id: 0, brand_id: 0 as number | undefined, name: '', category: '', description: '', is_active: true, community_ids: [] as number[],
})

function splitComma(text: string) {
  return text.split(/[,，]/).map((s) => s.trim()).filter(Boolean)
}

async function loadAll() {
  loading.value = true
  try {
    const [brandRows, productRows] = await Promise.all([listBrands(), listProducts()])
    brands.value = brandRows
    products.value = productRows
  } finally {
    loading.value = false
  }
}

async function loadPromoCommunities() {
  const rows = await listCommunities()
  promoCommunities.value = (rows || []).filter((c) => c.purpose === 'promo' && c.is_active)
}

function openProductEdit(row?: RedditBrandProduct) {
  if (row) {
    productForm.id = row.id
    productForm.brand_id = row.brand_id
    productForm.name = row.name
    productForm.category = row.category || ''
    productForm.description = row.description || ''
    productForm.is_active = row.is_active
    productForm.community_ids = [...(row.community_ids || [])]
    productTalkingText.value = (row.talking_points || []).join(', ')
    productKeywordsText.value = (row.keywords || []).join(', ')
  } else {
    productForm.id = 0
    productForm.brand_id = brands.value[0]?.id
    productForm.name = ''
    productForm.category = ''
    productForm.description = ''
    productForm.is_active = true
    productForm.community_ids = []
    productTalkingText.value = ''
    productKeywordsText.value = ''
  }
  productDialog.value = true
}

async function saveProduct() {
  if (!productForm.brand_id) {
    ElMessage.warning('请选择所属品牌')
    return
  }
  if (!productForm.name.trim()) {
    ElMessage.warning('请填写产品名')
    return
  }
  productSaving.value = true
  try {
    const payload = {
      name: productForm.name.trim(),
      category: productForm.category.trim(),
      description: productForm.description.trim(),
      talking_points: splitComma(productTalkingText.value),
      keywords: splitComma(productKeywordsText.value),
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
    await loadAll()
  } finally {
    productSaving.value = false
  }
}

async function toggleProduct(row: RedditBrandProduct, enabled: boolean) {
  await updateBrandProduct(row.id, { is_active: enabled })
  await loadAll()
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
  await loadAll()
}

onMounted(async () => {
  await Promise.all([loadAll(), loadPromoCommunities()])
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
