<template>
  <div class="page-container">
    <div class="page-toolbar">
      <el-select v-model="filterAlive" placeholder="存活状态" clearable style="width: 140px" @change="loadBacklinks">
        <el-option label="存活" :value="true" />
        <el-option label="丢失" :value="false" />
      </el-select>
      <el-button @click="loadBacklinks">刷新</el-button>
      <el-button type="warning" :loading="checkAllLoading" @click="handleCheckAll">批量检测</el-button>
      <el-button type="primary" @click="openDialog()">添加外链</el-button>
    </div>

    <el-table :data="backlinks" v-loading="loading" stripe>
      <el-table-column prop="id" label="ID" width="60" />
      <el-table-column prop="source_url" label="来源 URL" min-width="250" show-overflow-tooltip />
      <el-table-column prop="target_url" label="目标 URL" min-width="200" show-overflow-tooltip />
      <el-table-column prop="anchor_text" label="锚文本" width="150" show-overflow-tooltip />
      <el-table-column prop="domain_authority" label="DA" width="60" align="center" />
      <el-table-column prop="is_alive" label="状态" width="100" align="center">
        <template #default="{ row }">
          <el-tag :type="row.is_alive ? 'success' : 'danger'" size="small">
            {{ row.is_alive ? '存活' : '丢失' }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="first_seen_at" label="首次发现" width="170" />
      <el-table-column prop="last_checked_at" label="最后检测" width="170" />
      <el-table-column label="操作" width="200" fixed="right">
        <template #default="{ row }">
          <el-button size="small" :loading="checkingId === row.id" @click="handleCheck(row.id)">检测</el-button>
          <el-popconfirm title="确认删除？" @confirm="handleDelete(row.id)">
            <template #reference><el-button size="small" type="danger">删除</el-button></template>
          </el-popconfirm>
        </template>
      </el-table-column>
    </el-table>

    <!-- 添加/编辑对话框 -->
    <el-dialog v-model="dialogVisible" title="添加外链" width="500px">
      <el-form :model="form" label-width="100px">
        <el-form-item label="来源 URL" required>
          <el-input v-model="form.source_url" placeholder="https://example.com/article" />
        </el-form-item>
        <el-form-item label="目标 URL" required>
          <el-input v-model="form.target_url" placeholder="https://yoursite.com/page" />
        </el-form-item>
        <el-form-item label="锚文本">
          <el-input v-model="form.anchor_text" placeholder="点击这里" />
        </el-form-item>
        <el-form-item label="域名权重">
          <el-input-number v-model="form.domain_authority" :min="0" :max="100" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="formLoading" @click="handleSubmit">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import {
  listBacklinks,
  createBacklink,
  deleteBacklink,
  checkBacklink,
  checkAllBacklinks,
  type Backlink,
} from '@/api/backlinks'

const backlinks = ref<Backlink[]>([])
const loading = ref(false)
const filterAlive = ref<boolean | undefined>(undefined)

const dialogVisible = ref(false)
const formLoading = ref(false)
const form = ref({
  source_url: '',
  target_url: '',
  anchor_text: '',
  domain_authority: undefined as number | undefined,
})

const checkingId = ref<number | null>(null)
const checkAllLoading = ref(false)

async function loadBacklinks() {
  loading.value = true
  try {
    backlinks.value = await listBacklinks({
      is_alive: filterAlive.value ?? undefined,
      size: 100,
    })
  } finally {
    loading.value = false
  }
}

function openDialog() {
  form.value = { source_url: '', target_url: '', anchor_text: '', domain_authority: undefined }
  dialogVisible.value = true
}

async function handleSubmit() {
  if (!form.value.source_url || !form.value.target_url) {
    ElMessage.warning('来源 URL 和目标 URL 不能为空')
    return
  }
  formLoading.value = true
  try {
    await createBacklink({
      target_url: form.value.target_url,
      source_url: form.value.source_url,
      anchor_text: form.value.anchor_text || undefined,
      domain_authority: form.value.domain_authority,
    })
    ElMessage.success('外链添加成功')
    dialogVisible.value = false
    await loadBacklinks()
  } finally {
    formLoading.value = false
  }
}

async function handleDelete(id: number) {
  await deleteBacklink(id)
  ElMessage.success('已删除')
  await loadBacklinks()
}

async function handleCheck(id: number) {
  checkingId.value = id
  try {
    const result = await checkBacklink(id)
    const idx = backlinks.value.findIndex((b) => b.id === id)
    if (idx >= 0) backlinks.value[idx] = result
    ElMessage.success(`检测结果：${result.is_alive ? '存活' : '丢失'}`)
  } finally {
    checkingId.value = null
  }
}

async function handleCheckAll() {
  checkAllLoading.value = true
  try {
    const result = await checkAllBacklinks(100)
    ElMessage.success(`检测完成：存活 ${result.alive}，丢失 ${result.dead}`)
    await loadBacklinks()
  } finally {
    checkAllLoading.value = false
  }
}

onMounted(() => {
  loadBacklinks()
})
</script>
