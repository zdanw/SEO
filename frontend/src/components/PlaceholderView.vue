<template>
  <div class="page-container">
    <div class="page-title">{{ title }}</div>
    <el-alert
      type="success"
      :closable="false"
      show-icon
      :title="`P0 脚手架已就绪，该模块将在 ${phase} 实现：${desc}`"
    />
    <el-card shadow="never" style="margin-top: 16px">
      <el-empty :description="`${title} 模块 · 开发中`">
        <el-tag type="info">{{ phase }}</el-tag>
      </el-empty>
    </el-card>
  </div>
</template>

<script setup lang="ts">
defineProps<{ title: string; phase: string; desc: string }>()
</script>
