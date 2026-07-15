<template>
  <div class="message-list" ref="scrollContainerRef">
    <div v-if="messages.length === 0" class="empty-state">
      <el-icon size="64" color="#409EFC"><Service /></el-icon>
      <h3 class="welcome-title">欢迎使用云平台智能客服</h3>
      <p class="welcome-desc">
        我是您的专属 AI 助手，您可以直接向我提问，或者尝试以下典型场景：
      </p>
      <slot name="empty-actions"></slot>
    </div>

    <div
      v-for="(msg, index) in messages"
      :key="index"
      :class="['message-row', msg.role]"
    >
      <div :class="['msg-avatar', msg.role === 'user' ? 'user-avatar' : 'ai-avatar']">
        {{ msg.role === 'user' ? 'U' : 'AI' }}
      </div>
      <div class="message-bubble">
        <div v-if="msg.status" class="message-status">{{ msg.status }}</div>
        <div v-if="msg.content" class="message-content" v-html="renderMarkdown(msg.content)"></div>
      </div>
    </div>

    <div v-if="isLoading" class="message-row assistant">
      <div class="msg-avatar ai-avatar">AI</div>
      <div class="message-bubble loading">
        <el-icon class="is-loading"><Loading /></el-icon> 正在思考与调用工具中...
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { nextTick, ref } from 'vue'
import { ElIcon } from 'element-plus'
import { Loading, Service } from '@element-plus/icons-vue'
import type { ChatMessage } from '../composables/useChatSessions.js'
import { renderMarkdown } from '../utils/markdown.js'

defineProps<{
  messages: ChatMessage[]
  isLoading: boolean
}>()

const scrollContainerRef = ref<HTMLElement | null>(null)

const scrollToBottom = async () => {
  await nextTick()
  if (scrollContainerRef.value) {
    scrollContainerRef.value.scrollTop = scrollContainerRef.value.scrollHeight
  }
}

defineExpose({
  scrollToBottom,
})
</script>
