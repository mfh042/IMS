<script setup>
import { ref, onMounted, watch } from 'vue'
import { useRoute } from 'vue-router'
import { listAgents, runAgent } from '@/api'
import { ElMessage } from 'element-plus'
import EventInput from '@/components/EventInput.vue'
import FindingCard from '@/components/FindingCard.vue'
import JsonView from '@/components/JsonView.vue'

const route = useRoute()
const agents = ref([])
const activeCode = ref('')
const activeAgent = ref(null)
const event = ref({})
const incidentType = ref('')
const running = ref(false)
const result = ref(null)

const incidentTypeOptions = [
  { value: '', label: '自动判定（默认）' },
  { value: 'equipment_exception', label: '设备异常' },
  { value: 'quality_exception', label: '质量异常' },
  { value: 'material_exception', label: '物料异常' },
  { value: 'production_exception', label: '生产异常' },
  { value: 'mixed_exception', label: '混合异常' },
  { value: 'query', label: '查询/问答' },
  { value: 'standalone', label: '独立任务' }
]

const selectAgent = (code) => {
  activeCode.value = code
  activeAgent.value = agents.value.find((a) => a.code === code) || null
  result.value = null
}

const onRun = async () => {
  if (!activeCode.value) {
    ElMessage.warning('请先在左侧选择一个智能体')
    return
  }
  if (!event.value || !Object.keys(event.value).length) {
    ElMessage.warning('请输入或加载事件报文')
    return
  }
  running.value = true
  result.value = null
  try {
    result.value = await runAgent(activeCode.value, event.value, incidentType.value || null)
    ElMessage.success(`${result.value.agent_name} 执行完成`)
  } catch {
    /* interceptor handles message */
  } finally {
    running.value = false
  }
}

onMounted(async () => {
  agents.value = await listAgents()
  const initial = route.query.code || agents.value[0]?.code
  if (initial) selectAgent(initial)
})

watch(
  () => route.query.code,
  (code) => {
    if (code) selectAgent(code)
  }
)
</script>

<template>
  <div class="sim-page">
    <aside class="sim-sidebar ims-card">
      <h3 class="ims-section-title">
        <el-icon><Cpu /></el-icon>智能体列表
      </h3>
      <div class="agent-list">
        <div
          v-for="a in agents"
          :key="a.code"
          class="agent-item"
          :class="{ active: a.code === activeCode }"
          @click="selectAgent(a.code)"
        >
          <span class="agent-code">{{ a.code }}</span>
          <span class="agent-name">{{ a.name }}</span>
          <el-tag v-if="a.readonly" type="warning" size="small">只读</el-tag>
        </div>
      </div>
    </aside>

    <section class="sim-main">
      <div class="ims-card" v-if="activeAgent">
        <div class="agent-detail-head">
          <div>
            <el-tag size="large" type="primary">{{ activeAgent.code }}</el-tag>
            <span class="detail-name">{{ activeAgent.name }}</span>
          </div>
          <div class="detail-meta">
            <el-tag size="small">{{ activeAgent.domain }}</el-tag>
            <el-tag size="small" type="info">部署：{{ activeAgent.deployment }}</el-tag>
            <el-tag size="small" type="danger">风险上限：{{ activeAgent.risk_ceiling }}</el-tag>
            <el-tag v-if="activeAgent.first_release" size="small" type="success">首发</el-tag>
            <el-tag v-if="activeAgent.readonly" size="small" type="warning">只读取证</el-tag>
          </div>
        </div>
        <p class="detail-title">{{ activeAgent.title }}</p>
        <el-descriptions :column="3" border size="small" class="detail-desc">
          <el-descriptions-item label="子图">{{ activeAgent.subgraph }}</el-descriptions-item>
          <el-descriptions-item label="确认边界">{{ activeAgent.confirm_boundary }}</el-descriptions-item>
          <el-descriptions-item label="输入">{{ (activeAgent.inputs || []).join('、') }}</el-descriptions-item>
          <el-descriptions-item label="输出">{{ (activeAgent.outputs || []).join('、') }}</el-descriptions-item>
          <el-descriptions-item label="知识库">{{ (activeAgent.knowledge_bases || []).join('、') || '-' }}</el-descriptions-item>
          <el-descriptions-item label="必需知识">{{ (activeAgent.required_knowledge || []).join('、') || '-' }}</el-descriptions-item>
        </el-descriptions>
        <div class="detail-tools" v-if="activeAgent.tool_details?.length">
          <span class="ims-muted">可用工具：</span>
          <el-tag
            v-for="t in activeAgent.tool_details"
            :key="t.name"
            size="small"
            class="tool-tag"
          >
            {{ t.name }}
            <span v-if="t.risk" class="ims-muted">（{{ t.kind }}/{{ t.risk }}）</span>
          </el-tag>
        </div>
      </div>

      <div class="ims-card">
        <h3 class="ims-section-title">
          <el-icon><VideoPlay /></el-icon>运行模拟
        </h3>
        <EventInput v-model="event" />
        <div class="run-bar">
          <el-select v-model="incidentType" placeholder="事件类型（可选，覆盖自动判定）" style="width: 280px">
            <el-option v-for="o in incidentTypeOptions" :key="o.value" :label="o.label" :value="o.value" />
          </el-select>
          <el-button type="primary" :loading="running" @click="onRun">
            <el-icon><VideoPlay /></el-icon>运行 {{ activeCode }}
          </el-button>
        </div>
      </div>

      <div v-if="result" class="result-area">
        <div class="ims-card">
          <h3 class="ims-section-title">
            <el-icon><InfoFilled /></el-icon>事件 / 事件
          </h3>
          <el-descriptions :column="2" border size="small">
            <el-descriptions-item label="判定类型">
              <el-tag size="small" type="warning">{{ result.incident_type }}</el-tag>
            </el-descriptions-item>
            <el-descriptions-item label="trace_id">{{ result.incident.trace_id }}</el-descriptions-item>
            <el-descriptions-item label="incident_id">{{ result.incident.incident_id }}</el-descriptions-item>
            <el-descriptions-item label="severity">{{ result.incident.severity }}</el-descriptions-item>
            <el-descriptions-item label="实体引用" :span="2">
              <JsonView :data="result.incident.entity_refs" />
            </el-descriptions-item>
          </el-descriptions>
        </div>

        <div class="ims-card">
          <h3 class="ims-section-title">
            <el-icon><Document /></el-icon>取证结论（AgentFinding）
          </h3>
          <FindingCard :finding="result.finding" />
        </div>

        <div class="ims-card">
          <h3 class="ims-section-title">
            <el-icon><Tools /></el-icon>工具调用结果（{{ Object.keys(result.tool_results || {}).length }}）
          </h3>
          <el-empty v-if="!result.tool_results || !Object.keys(result.tool_results).length" description="未调用工具" :image-size="60" />
          <el-collapse v-else>
            <el-collapse-item v-for="(val, key) in result.tool_results" :key="key" :title="key" name="key">
              <JsonView :data="val" />
            </el-collapse-item>
          </el-collapse>
        </div>

        <div class="ims-card">
          <h3 class="ims-section-title">
            <el-icon><Collection /></el-icon>知识检索命中（{{ (result.knowledge_hits || []).length }}）
          </h3>
          <el-empty v-if="!result.knowledge_hits || !result.knowledge_hits.length" description="未命中知识库" :image-size="60" />
          <el-collapse v-else>
            <el-collapse-item
              v-for="(h, i) in result.knowledge_hits"
              :key="i"
              :name="i"
            >
              <template #title>
                <span>[{{ h.kb }}] {{ h.chunk?.chunk_id || ('hit#' + i) }}</span>
                <el-tag size="small" type="success" style="margin-left: 8px">score {{ h.score?.toFixed(3) }}</el-tag>
              </template>
              <JsonView :data="h" />
            </el-collapse-item>
          </el-collapse>
        </div>
      </div>
    </section>
  </div>
</template>

<style scoped>
.sim-page {
  display: flex;
  gap: 16px;
  padding: 20px;
  align-items: flex-start;
}
.sim-sidebar {
  width: 260px;
  flex-shrink: 0;
  position: sticky;
  top: 20px;
  max-height: calc(100vh - 100px);
  overflow: auto;
}
.agent-list {
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.agent-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 10px;
  border-radius: 6px;
  cursor: pointer;
  border: 1px solid transparent;
}
.agent-item:hover {
  background: #f5f7fa;
}
.agent-item.active {
  background: #ecf5ff;
  border-color: #b3d8ff;
}
.agent-code {
  font-family: monospace;
  font-weight: 700;
  color: var(--ims-primary);
  min-width: 28px;
}
.agent-name {
  font-size: 13px;
  flex: 1;
}
.sim-main {
  flex: 1;
  min-width: 0;
}
.agent-detail-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
  margin-bottom: 6px;
}
.detail-name {
  font-size: 18px;
  font-weight: 600;
  margin-left: 10px;
}
.detail-meta {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
}
.detail-title {
  color: var(--ims-muted);
  font-size: 13px;
  margin: 4px 0 12px;
}
.detail-desc {
  margin-bottom: 10px;
}
.detail-tools {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
}
.tool-tag {
  margin: 2px 4px 2px 0;
}
.run-bar {
  display: flex;
  gap: 12px;
  align-items: center;
  margin-top: 12px;
  flex-wrap: wrap;
}
.result-area {
  margin-top: 4px;
}
</style>