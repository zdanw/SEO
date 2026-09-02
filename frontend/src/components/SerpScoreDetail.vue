<template>
  <div v-if="detail?.overall_score != null" class="serp-detail">
    <div class="serp-header">
      <span class="serp-title">SERP 内容优化</span>
      <el-tag :type="scoreTagType(detail.overall_score)" size="small">
        {{ detail.overall_score }} / 100
      </el-tag>
      <el-tag v-if="detail.data_source" size="small" :type="detail.data_source === 'scrapingbee' ? 'success' : 'info'">
        {{ detail.data_source === 'scrapingbee' ? '真实 SERP' : '模拟基准' }}
      </el-tag>
    </div>

    <div v-if="detail.serp_benchmark" class="serp-bench">
      竞品均值 {{ detail.serp_benchmark.avg_word_count }} 词 ·
      建议 {{ detail.serp_benchmark.recommended_word_count }}+ 词 ·
      话题覆盖 {{ detail.coverage_percentage }}%
      <span v-if="detail.serp_benchmark.pages_analyzed">
        · 已分析 {{ detail.serp_benchmark.pages_analyzed }} 个排名页
      </span>
    </div>

    <div v-if="detail.top_competitors?.length" class="serp-block">
      <div class="block-label">Top 竞品（Google 自然排名）</div>
      <div v-for="c in detail.top_competitors" :key="c.url + c.position" class="competitor-card">
        <div class="competitor-head">
          <span class="comp-pos">#{{ c.position }}</span>
          <span class="comp-meta">{{ c.word_count }} 词 · {{ c.heading_count }} 标题</span>
        </div>
        <div class="comp-title">{{ c.title || c.domain }}</div>
        <a v-if="c.url" class="comp-link" :href="c.url" target="_blank" rel="noopener noreferrer">
          {{ c.url }}
        </a>
      </div>
    </div>

    <div v-if="detail.skipped_competitors?.length" class="serp-block skipped-block">
      <div class="block-label">已跳过排名（未纳入分析）</div>
      <div v-for="s in detail.skipped_competitors" :key="s.url + s.position" class="skipped-row">
        <span class="comp-pos">#{{ s.position }}</span>
        <span class="skipped-reason">{{ s.reason }}</span>
        <a v-if="s.url" class="comp-link" :href="s.url" target="_blank" rel="noopener noreferrer">{{ s.domain || s.url }}</a>
      </div>
    </div>

    <div v-for="(cat, name) in detail.categories" :key="name" class="serp-cat">
      <div class="cat-row">
        <span class="cat-name">{{ name }}</span>
        <span class="cat-score">{{ cat.score }}/{{ cat.max_score }}</span>
      </div>
      <el-progress
        :percentage="Math.round((cat.score / cat.max_score) * 100)"
        :stroke-width="8"
        :show-text="false"
        :color="progressColor(cat.score / cat.max_score)"
      />
      <div class="cat-detail">{{ cat.details }}</div>
    </div>

    <div v-if="detail.missing_topics?.length" class="serp-block">
      <div class="block-label">缺失话题</div>
      <el-tag v-for="t in detail.missing_topics" :key="t" size="small" type="warning" class="topic-tag">
        {{ t }}
      </el-tag>
    </div>

    <div v-if="detail.recommendations?.length" class="serp-block">
      <div class="block-label">优化建议</div>
      <div v-for="(r, i) in detail.recommendations" :key="i" class="rec-item">→ {{ r }}</div>
    </div>
  </div>
</template>

<script setup lang="ts">
import type { SerpDetail } from '@/api/seo'

defineProps<{
  detail?: SerpDetail | null
}>()

function scoreTagType(score: number) {
  return score >= 70 ? 'success' : score >= 50 ? 'warning' : 'danger'
}

function progressColor(ratio: number) {
  return ratio >= 0.7 ? '#67c23a' : ratio >= 0.5 ? '#e6a23c' : '#f56c6c'
}
</script>

<style lang="scss" scoped>
.serp-detail {
  margin-top: 10px;
  padding-top: 8px;
  border-top: 1px dashed #e4e7ed;
  .serp-header {
    display: flex;
    align-items: center;
    gap: 8px;
    margin-bottom: 6px;
  }
  .serp-title { font-weight: 600; font-size: 12px; }
  .serp-bench { font-size: 11px; color: #909399; margin-bottom: 8px; }
  .serp-cat { margin-bottom: 8px; }
  .cat-row {
    display: flex;
    justify-content: space-between;
    font-size: 12px;
    margin-bottom: 2px;
  }
  .cat-name { font-weight: 500; }
  .cat-score { color: #909399; }
  .cat-detail { font-size: 11px; color: #909399; margin-top: 2px; }
  .serp-block { margin-top: 8px; }
  .block-label { font-size: 12px; font-weight: 600; margin-bottom: 4px; }
  .topic-tag { margin: 0 4px 4px 0; }
  .competitor-card {
    font-size: 11px;
    margin-bottom: 8px;
    padding: 6px 8px;
    background: #f5f7fa;
    border-radius: 6px;
  }
  .competitor-head {
    display: flex;
    gap: 8px;
    align-items: center;
    flex-wrap: wrap;
    margin-bottom: 2px;
  }
  .comp-pos { color: #409eff; font-weight: 600; }
  .comp-title { color: #303133; font-weight: 500; margin: 2px 0; line-height: 1.4; }
  .comp-link {
    display: block;
    color: #409eff;
    font-size: 10px;
    word-break: break-all;
    text-decoration: none;
    &:hover { text-decoration: underline; }
  }
  .skipped-block { opacity: 0.85; }
  .skipped-row {
    font-size: 10px;
    margin-bottom: 4px;
    display: flex;
    gap: 6px;
    flex-wrap: wrap;
    align-items: baseline;
  }
  .skipped-reason { color: #e6a23c; }
  .rec-item { font-size: 11px; color: #e6a23c; margin-bottom: 3px; line-height: 1.4; }
}
</style>
