<script setup>
import { ref, onMounted } from 'vue'
import { getSettings } from '@/api'

const settings = ref(null)

onMounted(async () => {
  try {
    settings.value = await getSettings()
  } catch {
    /* ignore */
  }
})
</script>

<template>
  <el-container class="ims-app">
    <el-header class="ims-header">
      <div class="ims-brand">
        <span class="ims-logo">◈</span>
        <span class="ims-title">AI-IMS 工业多智能体系统</span>
      </div>
      <el-menu
        :default-active="$route.path"
        mode="horizontal"
        router
        class="ims-menu"
        background-color="transparent"
        text-color="#cfd8e3"
        active-text-color="#ffffff"
      >
        <el-menu-item index="/">
          <el-icon><DataAnalysis /></el-icon>
          <span>系统总览</span>
        </el-menu-item>
        <el-menu-item index="/simulator">
          <el-icon><Cpu /></el-icon>
          <span>单 Agent 模拟</span>
        </el-menu-item>
        <el-menu-item index="/orchestrator">
          <el-icon><Connection /></el-icon>
          <span>端到端编排</span>
        </el-menu-item>
      </el-menu>
      <div v-if="settings" class="ims-badges">
        <el-tag size="small" type="info" effect="dark">env: {{ settings.env }}</el-tag>
        <el-tag size="small" :type="settings.rollout_mode === 'shadow' ? 'warning' : 'success'" effect="dark">
          {{ settings.rollout_mode }}
        </el-tag>
        <el-tag size="small" type="primary" effect="dark">scope: {{ settings.agent_release_scope }}</el-tag>
        <el-tag size="small" :type="settings.llm_enabled ? 'success' : 'info'" effect="dark">
          LLM {{ settings.llm_enabled ? 'ON' : 'OFF' }}
        </el-tag>
      </div>
    </el-header>
    <el-main class="ims-main">
      <router-view />
    </el-main>
  </el-container>
</template>

<style scoped>
.ims-app {
  height: 100vh;
}
.ims-header {
  display: flex;
  align-items: center;
  gap: 32px;
  background: linear-gradient(90deg, #1f2d3d 0%, #2c3e50 100%);
  color: #fff;
  height: 60px;
  padding: 0 24px;
}
.ims-brand {
  display: flex;
  align-items: center;
  gap: 10px;
  white-space: nowrap;
}
.ims-logo {
  font-size: 22px;
  color: #4fc3f7;
}
.ims-title {
  font-size: 17px;
  font-weight: 600;
  letter-spacing: 0.5px;
}
.ims-menu {
  flex: 1;
  border-bottom: none;
  display: flex;
  justify-content: center;
}
.ims-menu :deep(.el-menu-item) {
  border-bottom: none !important;
  color: #cfd8e3;
}
.ims-menu :deep(.el-menu-item.is-active) {
  color: #fff !important;
  background: rgba(79, 195, 247, 0.15);
  border-radius: 6px;
}
.ims-badges {
  display: flex;
  gap: 6px;
}
.ims-main {
  padding: 0;
  overflow: auto;
  background: var(--ims-bg);
}
</style>