<template>
  <el-aside width="260px" class="sidebar">
    <div class="sidebar-header">
      <div class="brand">
        <div class="brand-logo">CA</div>
        <h2>Cloud Agent</h2>
      </div>
      <el-button type="primary" :icon="Plus" circle @click="emit('new-session')" />
    </div>
    <div class="session-list">
      <div
        v-for="session in sessions"
        :key="session.id"
        :class="['session-item', { active: currentSessionId === session.id }]"
        @click="emit('switch-session', session.id)"
      >
        <el-icon><ChatDotRound /></el-icon>
        <span class="session-name">{{ session.name }}</span>
      </div>
    </div>
    <div class="user-info">
      <div class="mini-avatar user-avatar">U</div>
      <span class="username">user_1001</span>
    </div>
  </el-aside>
</template>

<script setup lang="ts">
import { ElAside, ElButton, ElIcon } from 'element-plus'
import { ChatDotRound, Plus } from '@element-plus/icons-vue'
import type { ChatSession } from '../composables/useChatSessions.js'

defineProps<{
  sessions: ChatSession[]
  currentSessionId: string
}>()

const emit = defineEmits<{
  'new-session': []
  'switch-session': [id: string]
}>()
</script>
