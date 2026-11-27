<script setup>
defineProps({
  finding: { type: Object, required: true }
})

const confidenceColor = (c) => {
  if (c >= 0.7) return '#67c23a'
  if (c >= 0.4) return '#e6a23c'
  return '#f56c6c'
}
</script>

<template>
  <div class="finding-card">
    <div class="finding-head">
      <span class="finding-agent">{{ finding.agent_name }}</span>
      <span class="finding-summary">{{ finding.summary }}</span>
    </div>
    <div class="finding-conf">
      <span class="ims-muted">置信度</span>
      <el-progress
        :percentage="Math.round((finding.confidence || 0) * 100)"
        :color="confidenceColor(finding.confidence)"
        :stroke-width="10"
        style="width: 220px"
      />
    </div>

    <template v-if="finding.findings && finding.findings.length">
      <div class="finding-block">
        <div class="finding-label">核心结论</div>
        <ul><li v-for="(x, i) in finding.findings" :key="i">{{ x }}</li></ul>
      </div>
    </template>

    <template v-if="finding.hypotheses && finding.hypotheses.length">
      <div class="finding-block">
        <div class="finding-label">根因假设</div>
        <ul><li v-for="(x, i) in finding.hypotheses" :key="i">{{ x }}</li></ul>
      </div>
    </template>

    <template v-if="finding.suggested_next_steps && finding.suggested_next_steps.length">
      <div class="finding-block">
        <div class="finding-label">建议下一步</div>
        <ul><li v-for="(x, i) in finding.suggested_next_steps" :key="i">{{ x }}</li></ul>
      </div>
    </template>

    <div class="finding-grid">
      <template v-if="finding.evidence && finding.evidence.length">
        <div class="finding-block">
          <div class="finding-label">证据</div>
          <ul class="evidence-list">
            <li v-for="(x, i) in finding.evidence" :key="i">
              <JsonView :data="x" />
            </li>
          </ul>
        </div>
      </template>
      <template v-if="finding.data_gaps && finding.data_gaps.length">
        <div class="finding-block">
          <div class="finding-label">数据缺口</div>
          <el-tag v-for="(x, i) in finding.data_gaps" :key="i" type="warning" size="small" class="gap-tag">{{ x }}</el-tag>
        </div>
      </template>
      <template v-if="finding.risk_notes && finding.risk_notes.length">
        <div class="finding-block">
          <div class="finding-label">风险提示</div>
          <ul><li v-for="(x, i) in finding.risk_notes" :key="i">{{ x }}</li></ul>
        </div>
      </template>
      <template v-if="finding.knowledge_refs && finding.knowledge_refs.length">
        <div class="finding-block">
          <div class="finding-label">知识引用</div>
          <el-tag v-for="(x, i) in finding.knowledge_refs" :key="i" type="info" size="small" class="gap-tag">{{ x }}</el-tag>
        </div>
      </template>
    </div>
  </div>
</template>

<script>
import JsonView from './JsonView.vue'
export default { components: { JsonView } }
</script>

<style scoped>
.finding-card {
  border: 1px solid var(--ims-border);
  border-radius: 8px;
  padding: 14px 16px;
  background: #fff;
}
.finding-head {
  display: flex;
  align-items: baseline;
  gap: 12px;
  margin-bottom: 8px;
}
.finding-agent {
  font-weight: 600;
  color: var(--ims-primary);
  white-space: nowrap;
}
.finding-summary {
  color: var(--ims-text);
}
.finding-conf {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 12px;
}
.finding-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px 24px;
}
.finding-block {
  margin-bottom: 4px;
}
.finding-label {
  font-size: 12px;
  font-weight: 600;
  color: var(--ims-muted);
  margin-bottom: 4px;
  text-transform: uppercase;
  letter-spacing: 0.4px;
}
.finding-block ul {
  margin: 0;
  padding-left: 18px;
  font-size: 13px;
}
.evidence-list {
  list-style: none;
  padding-left: 0 !important;
}
.gap-tag {
  margin: 2px 4px 2px 0;
}
</style>