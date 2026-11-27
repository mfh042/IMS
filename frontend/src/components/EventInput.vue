<script setup>
import { ref, watch, onMounted } from 'vue'
import { listSamples, getSample } from '@/api'
import { ElMessage } from 'element-plus'

const props = defineProps({
  modelValue: { type: Object, default: () => ({}) }
})
const emit = defineEmits(['update:modelValue', 'parsed'])

const samples = ref([])
const text = ref('')
const parseError = ref('')
const selectedSample = ref('')

const pretty = (obj) => JSON.stringify(obj, null, 2)

const setText = (obj) => {
  text.value = pretty(obj || {})
  emit('update:modelValue', obj)
  emit('parsed', obj)
  parseError.value = ''
}

const parse = () => {
  if (!text.value.trim()) {
    parseError.value = '事件不能为空'
    emit('update:modelValue', null)
    return null
  }
  try {
    const obj = JSON.parse(text.value)
    parseError.value = ''
    emit('update:modelValue', obj)
    emit('parsed', obj)
    return obj
  } catch (e) {
    parseError.value = `JSON 解析失败：${e.message}`
    emit('update:modelValue', null)
    return null
  }
}

const onTextChange = () => {
  parse()
}

const loadSample = async (key) => {
  if (!key) return
  try {
    const s = await getSample(key)
    setText(s.event)
    ElMessage.success(`已加载样本：${s.label}`)
  } catch {
    /* handled by interceptor */
  }
}

const formatJson = () => {
  const obj = parse()
  if (obj) text.value = pretty(obj)
}

const clearAll = () => {
  text.value = ''
  selectedSample.value = ''
  parseError.value = ''
  emit('update:modelValue', null)
}

onMounted(async () => {
  if (props.modelValue && Object.keys(props.modelValue).length) {
    text.value = pretty(props.modelValue)
  }
  try {
    samples.value = await listSamples()
  } catch {
    /* ignore */
  }
})

watch(
  () => props.modelValue,
  (v) => {
    if (v && !text.value) text.value = pretty(v)
  }
)
</script>

<template>
  <div class="event-input">
    <div class="event-input-bar">
      <span class="ims-muted">上游事件报文（异构 JSON，将由 EventNormalizer 归一化）</span>
      <div class="event-input-actions">
        <el-select
          v-model="selectedSample"
          placeholder="一键加载示例事件"
          size="small"
          style="width: 300px"
          filterable
          @change="loadSample"
        >
          <el-option
            v-for="s in samples"
            :key="s.key"
            :label="s.label"
            :value="s.key"
          />
        </el-select>
        <el-button size="small" @click="formatJson">格式化</el-button>
        <el-button size="small" @click="clearAll">清空</el-button>
      </div>
    </div>
    <el-input
      v-model="text"
      type="textarea"
      :rows="10"
      placeholder='{"event_id":"...","event_type":"...","payload":{...}}'
      class="event-textarea ims-monospace"
      @input="onTextChange"
    />
    <div v-if="parseError" class="parse-error">{{ parseError }}</div>
    <div v-else class="parse-ok">✓ JSON 合法</div>
  </div>
</template>

<style scoped>
.event-input-bar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 8px;
  flex-wrap: wrap;
  gap: 8px;
}
.event-input-actions {
  display: flex;
  gap: 8px;
  align-items: center;
}
.event-textarea :deep(textarea) {
  font-family: 'JetBrains Mono', Consolas, monospace;
  font-size: 12px;
}
.parse-error {
  color: #f56c6c;
  font-size: 12px;
  margin-top: 4px;
}
.parse-ok {
  color: #67c23a;
  font-size: 12px;
  margin-top: 4px;
}
</style>