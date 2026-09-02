<template>
  <div v-if="audit?.score != null" class="tech-audit">
    <div class="tech-header">
      <span>技术 SEO（seoscan）</span>
      <el-tag :type="audit.score >= 70 ? 'success' : audit.score >= 50 ? 'warning' : 'danger'" size="small">
        {{ audit.score }} / 100
        <span v-if="audit.grade"> · {{ audit.grade }}</span>
      </el-tag>
    </div>

    <div v-for="cat in audit.categories" :key="cat.key" class="tech-cat">
      <span>{{ cat.name }}</span>
      <el-progress
        :percentage="cat.score"
        :stroke-width="8"
        style="width: 120px"
        :color="cat.score >= 70 ? '#67c23a' : cat.score >= 50 ? '#e6a23c' : '#f56c6c'"
      />
    </div>

    <div v-if="audit.top_issues?.length" class="tech-issues">
      <div class="issues-label">主要问题</div>
      <div v-for="(issue, i) in audit.top_issues.slice(0, 8)" :key="i" class="issue-item">• {{ issue }}</div>
    </div>
  </div>
</template>

<script setup lang="ts">
import type { TechnicalAudit } from '@/api/seo'

defineProps<{
  audit?: TechnicalAudit | null
}>()
</script>

<style lang="scss" scoped>
.tech-audit {
  margin-top: 10px;
  padding-top: 8px;
  border-top: 1px dashed #e4e7ed;
  .tech-header {
    display: flex;
    align-items: center;
    gap: 8px;
    font-weight: 600;
    font-size: 12px;
    margin-bottom: 8px;
  }
  .tech-cat {
    display: flex;
    align-items: center;
    justify-content: space-between;
    font-size: 12px;
    margin-bottom: 6px;
    gap: 8px;
  }
  .tech-issues { margin-top: 8px; }
  .issues-label { font-size: 12px; font-weight: 600; margin-bottom: 4px; }
  .issue-item { font-size: 11px; color: #f56c6c; margin-bottom: 2px; }
}
</style>
