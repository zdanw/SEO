<template>
  <div v-if="detail" class="ai-detail">
    <div class="ai-detect">
      <span>AI 检测分：</span>
      <el-tag :type="scoreTagType(score)" size="small">
        {{ score.toFixed(0) }} / 100
      </el-tag>
      <span class="ai-hint">（越低越像真人）</span>
    </div>

    <div v-if="detail.message" class="ai-msg">{{ detail.message }}</div>
    <div v-if="detail.engine" class="ai-engine">
      引擎：{{ engineLabel(detail.engine) }}
    </div>

    <!-- lmscan 明细 -->
    <div v-if="detail.lmscan?.score != null" class="ai-section">
      <div class="section-title">lmscan 统计检测 — {{ detail.lmscan.score }} 分</div>
      <div v-if="detail.lmscan.verdict" class="sub-line">判定：{{ detail.lmscan.verdict }}</div>
      <div v-if="detail.lmscan.model_attribution?.length" class="sub-block">
        <div class="sub-label">可能模型来源</div>
        <div v-for="(m, i) in detail.lmscan.model_attribution" :key="i" class="tag-row">
          <el-tag size="small" type="info">{{ m.model }} {{ m.confidence }}%</el-tag>
        </div>
      </div>
      <div v-if="detail.lmscan.high_risk_sentences?.length" class="sub-block">
        <div class="sub-label">高风险句子</div>
        <div
          v-for="(s, i) in detail.lmscan.high_risk_sentences"
          :key="i"
          class="risk-sentence"
        >
          <el-tag size="small" :type="s.score >= 70 ? 'danger' : 'warning'">{{ s.score }}分</el-tag>
          <span>{{ s.text }}</span>
        </div>
      </div>
    </div>

    <!-- Signs of AI 明细 -->
    <div v-if="detail.signs_of_ai?.patterns?.length" class="ai-section">
      <div class="section-title">
        Signs of AI 模式 — {{ detail.signs_of_ai.score }} 分
        （命中 {{ detail.signs_of_ai.patterns_matched }} 类）
      </div>
      <div v-for="(p, i) in detail.signs_of_ai.patterns" :key="i" class="pattern-item">
        <div class="pattern-head">
          <el-tag size="small" :type="severityType(p.severity)">{{ p.name }}</el-tag>
          <span class="pattern-count">×{{ p.count }}</span>
        </div>
        <div v-if="p.matches?.length" class="pattern-matches">
          {{ p.matches.join('、') }}
        </div>
        <div v-if="p.suggestion" class="pattern-sugg">→ {{ p.suggestion }}</div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import type { AiDetectDetail } from '@/api/seo'

const props = defineProps<{
  detail?: AiDetectDetail | null
  score: number
}>()

const score = computed(() => props.score ?? 0)

function scoreTagType(s: number) {
  return s < 30 ? 'success' : s < 60 ? 'warning' : 'danger'
}

function severityType(sev: string) {
  return sev === 'high' ? 'danger' : sev === 'medium' ? 'warning' : 'info'
}

function engineLabel(engine: string) {
  return {
    hybrid: 'lmscan + Signs of AI 混合',
    lmscan: 'lmscan',
    signs_of_ai: 'Signs of AI',
    heuristic: '内置启发式',
    none: '未检测',
  }[engine] || engine
}
</script>

<style lang="scss" scoped>
.ai-detail {
  .ai-detect {
    display: flex;
    align-items: center;
    gap: 6px;
    flex-wrap: wrap;
  }
  .ai-hint {
    color: #909399;
    font-size: 12px;
  }
  .ai-msg, .ai-engine {
    margin-top: 6px;
    font-size: 12px;
    color: #606266;
  }
  .ai-section {
    margin-top: 10px;
    padding-top: 8px;
    border-top: 1px dashed #e4e7ed;
  }
  .section-title {
    font-weight: 600;
    font-size: 12px;
    margin-bottom: 6px;
    color: #303133;
  }
  .sub-line, .sub-label {
    font-size: 12px;
    color: #909399;
    margin-bottom: 4px;
  }
  .sub-block { margin-top: 6px; }
  .tag-row { margin-bottom: 4px; }
  .risk-sentence {
    font-size: 12px;
    color: #606266;
    margin-bottom: 6px;
    line-height: 1.4;
    display: flex;
    gap: 6px;
    align-items: flex-start;
  }
  .pattern-item {
    margin-bottom: 8px;
    font-size: 12px;
  }
  .pattern-head {
    display: flex;
    align-items: center;
    gap: 6px;
    margin-bottom: 2px;
  }
  .pattern-count { color: #909399; }
  .pattern-matches {
    color: #606266;
    margin: 2px 0;
    word-break: break-word;
  }
  .pattern-sugg {
    color: #e6a23c;
    font-size: 11px;
  }
}
</style>
