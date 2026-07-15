import type { Ref } from 'vue'
import type { ChatMessage } from './useChatSessions.js'

export interface StreamChatPayload {
  [key: string]: unknown
}

export interface StreamChatOptions {
  query: string
  sessionId: string
  signal: AbortSignal
  onPayload: (payload: StreamChatPayload) => void
}

export interface AssistantStreamState {
  content: string
  status: string
  done: boolean
}

export interface UseChatControllerOptions {
  currentSessionId: Ref<string>
  addMessage: (message: ChatMessage) => ChatMessage
  persist: () => void
  scrollToBottom?: () => void | Promise<void>
  streamChatImpl?: (options: StreamChatOptions) => Promise<void>
  formatErrorMessage?: (error: unknown, timeoutMs?: number) => string
  applyPayload?: (state: AssistantStreamState, payload: StreamChatPayload) => AssistantStreamState
  createStreamState?: () => AssistantStreamState
  chatTimeoutMs?: number
  setTimeoutImpl?: (callback: () => void, timeoutMs: number) => unknown
  clearTimeoutImpl?: (timeoutId: unknown) => void
  createAbortController?: () => AbortController
  logger?: Pick<Console, 'error'>
}

export interface UseChatControllerResult {
  inputQuery: Ref<string>
  isLoading: Ref<boolean>
  sendQuery: (query: string) => Promise<void>
}

export function useChatController(options: UseChatControllerOptions): UseChatControllerResult
