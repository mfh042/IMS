import { createRouter, createWebHistory } from 'vue-router'
import Dashboard from '@/views/Dashboard.vue'
import AgentSimulator from '@/views/AgentSimulator.vue'
import Orchestrator from '@/views/Orchestrator.vue'

const routes = [
  { path: '/', name: 'dashboard', component: Dashboard, meta: { title: '系统总览' } },
  { path: '/simulator', name: 'simulator', component: AgentSimulator, meta: { title: '单 Agent 模拟' } },
  { path: '/orchestrator', name: 'orchestrator', component: Orchestrator, meta: { title: '端到端编排' } }
]

const router = createRouter({
  history: createWebHistory(),
  routes
})

router.afterEach((to) => {
  document.title = `AI-IMS · ${to.meta.title || '工业多智能体系统'}`
})

export default router