import type { Ref } from 'vue'

export const CHAT_SESSIONS_STORAGE_KEY: string

export interface ChatSession {
  id: string
  name: string
}

export interface ChatMessage {
  role: 'user' | 'assistant'
  content: string
  status?: string
}

export interface ChatStorage {
  getItem(key: string): string | null
  setItem(key: string, value: string): void
  removeItem(key: string): void
}

export interface UseChatSessionsOptions {
  storage?: ChatStorage
  storageKey?: string
  now?: () => number
}

export interface UseChatSessionsResult {
  sessions: Ref<ChatSession[]>
  currentSessionId: Ref<string>
  messages: Ref<ChatMessage[]>
  createNewSession: () => ChatSession
  switchSession: (id: string) => void
  addMessage: (message: ChatMessage) => ChatMessage
  persist: () => void
}

export function createMemoryStorage(initialItems?: Record<string, string>): ChatStorage
export function useChatSessions(options?: UseChatSessionsOptions): UseChatSessionsResult
