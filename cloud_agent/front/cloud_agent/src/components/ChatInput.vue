<template>
  <div class="input-area">
    <el-input
      v-model="query"
      type="textarea"
      :rows="3"
      placeholder="请输入您的问题，Shift + Enter 换行，Enter 发送"
      @keydown.enter="handleEnter"
      :disabled="isLoading"
    />
    <el-button
      type="primary"
      class="send-btn"
      :icon="Position"
      :loading="isLoading"
      @click="emit('send', query)"
      :disabled="!query.trim()"
    >
      发送
    </el-button>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { ElButton, ElInput } from 'element-plus'
import { Position } from '@element-plus/icons-vue'

const props = defineProps<{
  modelValue: string
  isLoading: boolean
}>()

const emit = defineEmits<{
  'update:modelValue': [value: string]
  send: [query: string]
}>()

const query = computed({
  get: () => props.modelValue,
  set: (value: string) => emit('update:modelValue', value),
})

const handleEnter = (event: Event | KeyboardEvent) => {
  if ('shiftKey' in event && event.shiftKey) return

  event.preventDefault()
  if (query.value.trim() && !props.isLoading) {
    emit('send', query.value)
  }
}
</script>
