<template>
  <div class="chat-container">
    <el-container class="app-shell">
      <ChatSidebar
        :sessions="sessions"
        :current-session-id="currentSessionId"
        @new-session="createNewSession"
        @switch-session="switchSession"
      />

      <el-main class="chat-main">
        <div class="chat-header">
          <div class="header-title">企业云智能客服</div>
          <div class="header-subtitle">Multi-Agent · Billing · Promotion · FinOps</div>
        </div>

        <MessageList
          ref="messageListRef"
          :messages="messages"
          :is-loading="isLoading"
        >
          <template #empty-actions>
            <ScenarioGrid
              :scenarios="scenarioGroups"
              @select-query="sendQuery"
            />
          </template>
        </MessageList>

        <ChatInput
          v-model="inputQuery"
          :is-loading="isLoading"
          @send="sendQuery"
        />
      </el-main>
    </el-container>
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { ElContainer, ElMain } from 'element-plus'
import ChatInput from './components/ChatInput.vue'
import ChatSidebar from './components/ChatSidebar.vue'
import MessageList from './components/MessageList.vue'
import ScenarioGrid from './components/ScenarioGrid.vue'
import { useChatController } from './composables/useChatController.js'
import { useChatSessions } from './composables/useChatSessions.js'
import { scenarioGroups } from './data/scenarios.js'

interface MessageListHandle {
  scrollToBottom: () => Promise<void>
}

const messageListRef = ref<MessageListHandle | null>(null)
const {
  sessions,
  currentSessionId,
  messages,
  createNewSession,
  switchSession,
  addMessage,
  persist,
} = useChatSessions()

const { inputQuery, isLoading, sendQuery } = useChatController({
  currentSessionId,
  addMessage,
  persist,
  scrollToBottom: () => messageListRef.value?.scrollToBottom(),
})
</script>

<style scoped src="./assets/chat.css"></style>
