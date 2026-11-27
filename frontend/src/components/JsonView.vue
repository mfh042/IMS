<script setup>
import { computed } from 'vue'

const props = defineProps({
  data: { type: [Object, Array, String, Number, Boolean], default: null }
})

const text = computed(() => {
  try {
    return typeof props.data === 'string' ? props.data : JSON.stringify(props.data, null, 2)
  } catch {
    return String(props.data)
  }
})
</script>

<template>
  <pre class="ims-json ims-monospace">{{ text }}</pre>
</template>