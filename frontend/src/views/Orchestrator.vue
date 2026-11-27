<script setup>
import { ref, computed, onMounted } from 'vue'
import { runEvent, resumeConfirm, getGraph, getAudit } from '@/api'
import { ElMessage } from 'element-plus'
import EventInput from '@/components/EventInput.vue'
import FindingCard from '@/components/FindingCard.vue'
import JsonView from '@/components/JsonView.vue'

const graphNodes = ref([])
const event = ref({})
const threadId = ref('')
const running = ref(false)
const result = ref(null)
const usedThreadId = ref('')

const confirmVisible = ref(false)
const confirmForm = ref({ approved: true, decision_by: '', roles: ['system'], note: '' })
const auditVisible = ref(false)
const auditData = ref(null)
const auditLoading = ref(false)

const executedMap = computed(() => {
  const map = {}
  for (const t of result.value?.node_trace || []) map[t.node] = t
  return map
})
const errorNodes = computed(() => {
  const set = new Set()
  for (const e of result.value?.errors || []) if (e.node) set.add(e.node)
  return set
})
const nodeStatus = (nodeId) => {
  if (errorNodes.value.has(nodeId)) return 'error'
  if (executedMap.value[nodeId]) return 'done'
  return 'skip'
}

const riskType = (r) => ({ low: 'success', medium: 'warning', high: 'danger' }[r] || 'info')

const onRun = async () => {
  if (!event.value || !Object.keys(event.value).length) {
    ElMessage.warning('请输入或加载事件报文')
    return
  }
  running.value = true
  result.value = null
  try {
    const tid = threadId.value || event.value.trace_id || ''
    usedThreadId.value = tid
    result.value = await runEvent(event.value, tid || null)
    ElMessage.success('编排执行完成')
  } catch {
    /* handled */
  } finally {
    running.value = false
  }
}

const openConfirm = () => {
  confirmForm.value.decision_by = confirmForm.value.decision_by || 'operator'
  confirmVisible.value = true
}

const submitConfirm = async () => {
  if (!usedThreadId.value) {
    ElMessage.warning('缺少 thread_id，无法恢复')
    return
  }
  try {
    const r = await resumeConfirm(usedThreadId.value, confirmForm.value)
    result.value = r
    confirmVisible.value = false
    ElMessage.success('已恢复执行')
  } catch {
    /* handled */
  }
}

const viewAudit = async () => {
  const traceId = result.value?.incident?.trace_id
  if (!traceId) return
  auditLoading.value = true
  auditVisible.value = true
  try {
    auditData.value = await getAudit(traceId)
  } catch {
    auditData.value = null
  } finally {
    auditLoading.value = false
  }
}

onMounted(async () => {
  try {
    graphNodes.value = (await getGraph()).nodes
  } catch {
    /* ignore */
  }
})
</script>

<template>
  <div class="ims-page">
    <h2 class="ims-section-title">端到端编排</h2>

    <div class="ims-card">
      <h3 class="ims-section-title"><el-icon><Promotion /></el-icon>事件接入</h3>
      <EventInput v-model="event" />
      <div class="run-bar">
        <el-input v-model="threadId" placeholder="thread_id（可选，用于中断恢复；默认取 event.trace_id）" style="width: 420px" />
        <el-button type="primary" :loading="running" @click="onRun">
          <el-icon><VideoPlay /></el-icon>运行完整编排
        </el-button>
        <el-button v-if="result" @click="viewAudit">
          <el-icon><Document /></el-icon>查看审计轨迹
        </el-button>
      </div>
    </div>

    <template v-if="result">
      <div class="ims-card">
        <h3 class="ims-section-title"><el-icon><Share /></el-icon>节点执行轨迹（{{ Object.keys(executedMap).length }}/{{ graphNodes.length }}）</h3>
        <div class="trace-list">
          <div v-for="n in graphNodes" :key="n.id" class="trace-row" :class="nodeStatus(n.id)">
            <div class="trace-node">
              <span class="node-code">{{ n.id }}</span>
              <span class="node-name">{{ n.name }}</span>
            </div>
            <div class="trace-status">
              <el-tag v-if="nodeStatus(n.id) === 'done'" type="success" size="small">已执行</el-tag>
              <el-tag v-else-if="nodeStatus(n.id) === 'error'" type="danger" size="small">错误</el-tag>
              <el-tag v-else type="info" size="small">未执行</el-tag>
            </div>
            <div class="trace-payload" v-if="executedMap[n.id]">
              <span class="ims-muted">{{ executedMap[n.id].at }}</span>
              <JsonView :data="executedMap[n.id].payload" />
            </div>
          </div>
        </div>
      </div>

      <div class="ims-card">
        <h3 class="ims-section-title"><el-icon><InfoFilled /></el-icon>事件与路由</h3>
        <el-descriptions :column="2" border size="small">
          <el-descriptions-item label="判定类型">
            <el-tag size="small" type="warning">{{ result.incident_type }}</el-tag>
          </el-descriptions-item>
          <el-descriptions-item label="trace_id">{{ result.incident?.trace_id }}</el-descriptions-item>
          <el-descriptions-item label="incident_id">{{ result.incident?.incident_id }}</el-descriptions-item>
          <el-descriptions-item label="severity">{{ result.incident?.severity }}</el-descriptions-item>
          <el-descriptions-item label="运行 Agent" :span="2">
            <el-tag v-for="c in result.agent_codes" :key="c" size="small" type="primary" style="margin:2px">{{ c }}</el-tag>
            <span v-if="!result.agent_codes?.length" class="ims-muted">-</span>
          </el-descriptions-item>
        </el-descriptions>
      </div>

      <div class="ims-card">
        <h3 class="ims-section-title"><el-icon><Document /></el-icon>各智能体取证结论（{{ Object.keys(result.agent_findings || {}).length }}）</h3>
        <div v-if="result.agent_findings && Object.keys(result.agent_findings).length" class="findings-grid">
          <FindingCard v-for="(f, code) in result.agent_findings" :key="code" :finding="f" />
        </div>
        <el-empty v-else description="无智能体结论" :image-size="60" />
      </div>

      <div class="ims-card" v-if="result.resolution_plan">
        <h3 class="ims-section-title"><el-icon><List /></el-icon>处置方案（ResolutionPlan）</h3>
        <el-descriptions :column="2" border size="small">
          <el-descriptions-item label="问题概述" :span="2">{{ result.resolution_plan.problem_summary }}</el-descriptions-item>
          <el-descriptions-item label="置信度">
            <el-progress :percentage="Math.round((result.resolution_plan.confidence || 0) * 100)" :stroke-width="10" style="width:200px" />
          </el-descriptions-item>
          <el-descriptions-item label="需人工确认">
            <el-tag :type="result.resolution_plan.need_human_confirm ? 'warning' : 'success'" size="small">
              {{ result.resolution_plan.need_human_confirm ? '是' : '否' }}
            </el-tag>
          </el-descriptions-item>
          <el-descriptions-item label="校验说明" :span="2">{{ result.resolution_plan.validity_note || '-' }}</el-descriptions-item>
        </el-descriptions>
        <h4 class="sub-title">动作清单（{{ result.resolution_plan.actions?.length || 0 }}）</h4>
        <el-table :data="result.resolution_plan.actions" size="small" border>
          <el-table-column prop="action_id" label="ID" width="100" />
          <el-table-column prop="action_type" label="类型" width="170" />
          <el-table-column prop="title" label="标题" min-width="180" />
          <el-table-column prop="description" label="说明" min-width="220" show-overflow-tooltip />
          <el-table-column label="风险" width="80" align="center">
            <template #default="{ row }"><el-tag :type="riskType(row.risk_level)" size="small">{{ row.risk_level }}</el-tag></template>
          </el-table-column>
          <el-table-column prop="tool_name" label="工具" width="150" />
          <el-table-column label="需确认" width="80" align="center">
            <template #default="{ row }">
              <el-tag v-if="row.requires_human_confirm" type="warning" size="small">是</el-tag>
              <span v-else class="ims-muted">否</span>
            </template>
          </el-table-column>
        </el-table>
      </div>

      <div class="ims-card">
        <h3 class="ims-section-title"><el-icon><Checked /></el-icon>执行与确认</h3>
        <el-descriptions :column="2" border size="small">
          <el-descriptions-item label="确认状态">
            <el-tag size="small" :type="result.confirm_status === 'approved' ? 'success' : 'warning'">{{ result.confirm_status || '-' }}</el-tag>
          </el-descriptions-item>
          <el-descriptions-item label="已执行动作">{{ result.executed_actions?.length || 0 }}</el-descriptions-item>
        </el-descriptions>
        <div v-if="result.executed_actions?.length" style="margin-top:10px">
          <JsonView :data="result.executed_actions" />
        </div>
        <div v-if="result.__interrupt__" class="interrupt-box">
          <el-alert type="warning" :closable="false" title="图执行已挂起，等待人工确认">
            <template #default>
              <JsonView :data="result.__interrupt__" />
            </template>
          </el-alert>
          <el-button type="primary" style="margin-top:10px" @click="openConfirm">处理确认</el-button>
        </div>
      </div>

      <div class="ims-card">
        <h3 class="ims-section-title"><el-icon><ChatDotRound /></el-icon>最终结论</h3>
        <div class="final-answer">{{ result.final_answer || '（无）' }}</div>
      </div>
    </template>

    <el-dialog v-model="confirmVisible" title="人工确认" width="520px">
      <el-form :model="confirmForm" label-width="100px">
        <el-form-item label="决策">
          <el-radio-group v-model="confirmForm.approved">
            <el-radio :value="true">批准执行</el-radio>
            <el-radio :value="false">拒绝</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="决策人">
          <el-input v-model="confirmForm.decision_by" />
        </el-form-item>
        <el-form-item label="角色">
          <el-input v-model="confirmForm.roles" placeholder="逗号分隔，如 maintenance_lead,quality_lead" />
        </el-form-item>
        <el-form-item label="备注">
          <el-input v-model="confirmForm.note" type="textarea" :rows="2" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="confirmVisible = false">取消</el-button>
        <el-button type="primary" @click="submitConfirm">提交并恢复</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="auditVisible" title="审计轨迹" width="780px" top="5vh">
      <div v-loading="auditLoading">
        <div v-if="auditData?.records?.length">
          <el-timeline>
            <el-timeline-item
              v-for="(r, i) in auditData.records"
              :key="i"
              :timestamp="r.at"
              :type="r.decision === 'denied' ? 'danger' : 'primary'"
            >
              <div class="audit-item">
                <strong>{{ r.actor }}</strong> · {{ r.action }}
                <span v-if="r.reason" class="ims-muted"> · {{ r.reason }}</span>
                <JsonView :data="r.input_snapshot || r.output_snapshot || {}" />
              </div>
            </el-timeline-item>
          </el-timeline>
        </div>
        <el-empty v-else description="暂无审计记录" :image-size="60" />
      </div>
    </el-dialog>
  </div>
</template>

<style scoped>
.run-bar {
  display: flex;
  gap: 12px;
  align-items: center;
  margin-top: 12px;
  flex-wrap: wrap;
}
.trace-list {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.trace-row {
  display: grid;
  grid-template-columns: 1fr 90px 1fr;
  gap: 12px;
  align-items: flex-start;
  padding: 8px 10px;
  border: 1px solid var(--ims-border);
  border-radius: 6px;
  background: #fff;
}
.trace-row.done { border-left: 3px solid #67c23a; }
.trace-row.error { border-left: 3px solid #f56c6c; background: #fef0f0; }
.trace-row.skip { opacity: 0.55; }
.node-code { font-family: monospace; font-weight: 700; color: var(--ims-primary); margin-right: 8px; }
.node-name { font-size: 13px; }
.trace-payload { font-size: 12px; }
.findings-grid {
  display: grid;
  grid-template-columns: 1fr;
  gap: 12px;
}
.sub-title { font-size: 13px; font-weight: 600; margin: 14px 0 8px; }
.interrupt-box { margin-top: 12px; }
.final-answer {
  background: #f0f9ff;
  border: 1px solid #b3d8ff;
  border-radius: 6px;
  padding: 12px 14px;
  font-size: 14px;
  line-height: 1.6;
  white-space: pre-wrap;
}
.audit-item { font-size: 13px; }
.audit-item :deep(pre) { margin-top: 6px; }
</style>