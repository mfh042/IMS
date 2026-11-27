<script setup>
import { ref, onMounted, computed } from 'vue'
import { useRouter } from 'vue-router'
import { getSettings, getGraph, listAgents } from '@/api'

const router = useRouter()
const settings = ref(null)
const graph = ref({ nodes: [] })
const agents = ref([])
const loading = ref(false)

const phases = ['接入', '规划', '取证', '执行', '收尾']
const phaseColor = {
  接入: '#909399',
  规划: '#1d6fe0',
  取证: '#67c23a',
  执行: '#e6a23c',
  收尾: '#909399'
}

const nodesByPhase = computed(() => {
  const map = {}
  for (const p of phases) map[p] = []
  for (const n of graph.value.nodes) {
    if (map[n.phase]) map[n.phase].push(n)
  }
  return map
})

const goSimulate = (code) => router.push({ path: '/simulator', query: { code } })

const domainTag = (domain) => {
  const map = {
    equipment: '设备侧',
    ai_manufacturing: 'AI+制造',
    cross_domain: '跨域',
    industrial_software: '工业软件',
    product_ai: '产品 AI',
    control: '管控',
    common: '通用'
  }
  return map[domain] || domain
}

onMounted(async () => {
  loading.value = true
  try {
    const [s, g, a] = await Promise.all([getSettings(), getGraph(), listAgents()])
    settings.value = s
    graph.value = g
    agents.value = a
  } finally {
    loading.value = false
  }
})
</script>

<template>
  <div class="ims-page" v-loading="loading">
    <h2 class="ims-section-title">系统总览</h2>

    <el-row :gutter="16" class="stat-row">
      <el-col :span="6">
        <el-card shadow="hover" class="stat-card">
          <div class="stat-label">运行环境</div>
          <div class="stat-value">{{ settings?.env || '-' }}</div>
        </el-card>
      </el-col>
      <el-col :span="6">
        <el-card shadow="hover" class="stat-card">
          <div class="stat-label">灰度模式</div>
          <div class="stat-value">
            <el-tag :type="settings?.rollout_mode === 'shadow' ? 'warning' : 'success'">
              {{ settings?.rollout_mode }}
            </el-tag>
          </div>
        </el-card>
      </el-col>
      <el-col :span="6">
        <el-card shadow="hover" class="stat-card">
          <div class="stat-label">智能体发布范围</div>
          <div class="stat-value">{{ settings?.agent_release_scope || '-' }}</div>
        </el-card>
      </el-col>
      <el-col :span="6">
        <el-card shadow="hover" class="stat-card">
          <div class="stat-label">编排节点 / 子智能体</div>
          <div class="stat-value">{{ graph.nodes.length }} / {{ agents.length }}</div>
        </el-card>
      </el-col>
    </el-row>

    <div class="ims-card">
      <h3 class="ims-section-title">
        <el-icon><Share /></el-icon>主图编排（14 节点）
      </h3>
      <div class="graph-flow">
        <template v-for="(p, idx) in phases" :key="p">
          <div class="phase-col">
            <div class="phase-title" :style="{ color: phaseColor[p] }">{{ p }}</div>
            <div v-for="n in nodesByPhase[p]" :key="n.id" class="node-pill" :style="{ borderColor: phaseColor[p] }">
              <div class="node-id">{{ n.id }}</div>
              <div class="node-name">{{ n.name }}</div>
            </div>
          </div>
          <div v-if="idx < phases.length - 1" class="phase-arrow">→</div>
        </template>
      </div>
    </div>

    <div class="ims-card">
      <h3 class="ims-section-title">
        <el-icon><List /></el-icon>智能体目录（{{ agents.length }} 个）
      </h3>
      <el-table :data="agents" stripe size="small" border>
        <el-table-column prop="code" label="编号" width="70" />
        <el-table-column prop="name" label="名称" width="150" />
        <el-table-column label="域" width="110">
          <template #default="{ row }">{{ domainTag(row.domain) }}</template>
        </el-table-column>
        <el-table-column prop="title" label="职责" min-width="220" show-overflow-tooltip />
        <el-table-column label="首发" width="70" align="center">
          <template #default="{ row }">
            <el-tag v-if="row.first_release" type="success" size="small">是</el-tag>
            <el-tag v-else type="info" size="small">否</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="只读" width="70" align="center">
          <template #default="{ row }">
            <el-tag v-if="row.readonly" type="warning" size="small">只读</el-tag>
            <span v-else class="ims-muted">-</span>
          </template>
        </el-table-column>
        <el-table-column label="工具" width="90" align="center">
          <template #default="{ row }">{{ row.tools?.length || 0 }}</template>
        </el-table-column>
        <el-table-column label="风险上限" width="90" align="center">
          <template #default="{ row }">
            <el-tag size="small">{{ row.risk_ceiling }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="110" fixed="right">
          <template #default="{ row }">
            <el-button size="small" type="primary" @click="goSimulate(row.code)">去模拟</el-button>
          </template>
        </el-table-column>
      </el-table>
    </div>
  </div>
</template>

<style scoped>
.stat-row {
  margin-bottom: 16px;
}
.stat-card {
  text-align: center;
}
.stat-label {
  font-size: 12px;
  color: var(--ims-muted);
  margin-bottom: 6px;
}
.stat-value {
  font-size: 20px;
  font-weight: 600;
}
.graph-flow {
  display: flex;
  align-items: stretch;
  gap: 8px;
  overflow-x: auto;
  padding: 8px 0;
}
.phase-col {
  display: flex;
  flex-direction: column;
  gap: 8px;
  min-width: 170px;
}
.phase-title {
  font-size: 12px;
  font-weight: 700;
  text-align: center;
  letter-spacing: 2px;
}
.node-pill {
  border: 1.5px solid;
  border-radius: 8px;
  padding: 8px 10px;
  background: #fafbfc;
}
.node-id {
  font-size: 11px;
  color: var(--ims-muted);
  font-family: monospace;
}
.node-name {
  font-size: 13px;
  font-weight: 600;
  margin-top: 2px;
}
.phase-arrow {
  display: flex;
  align-items: center;
  font-size: 20px;
  color: var(--ims-muted);
  font-weight: 700;
}
</style>