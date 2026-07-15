import { ref } from 'vue'
import { CHAT_TIMEOUT_MS, formatChatErrorMessage, streamChat } from '../utils/chatStream.js'
import { applySsePayload, createAssistantStreamState } from '../utils/sseEvents.js'

export function useChatController({
  currentSessionId,
  addMessage,
  persist,
  scrollToBottom = () => {},
  streamChatImpl = streamChat,
  formatErrorMessage = formatChatErrorMessage,
  applyPayload = applySsePayload,
  createStreamState = createAssistantStreamState,
  chatTimeoutMs = CHAT_TIMEOUT_MS,
  setTimeoutImpl = globalThis.setTimeout.bind(globalThis),
  clearTimeoutImpl = globalThis.clearTimeout.bind(globalThis),
  createAbortController = () => new AbortController(),
  logger = console,
}) {
  const inputQuery = ref('')
  const isLoading = ref(false)

  const scroll = () => {
    void scrollToBottom()
  }

  const sendQuery = async (query) => {
    if (!query.trim()) return

    const text = query.trim()
    inputQuery.value = ''

    addMessage({ role: 'user', content: text })
    scroll()

    isLoading.value = true

    const assistantMessage = addMessage({ role: 'assistant', content: '', status: '' })
    const assistantStreamState = createStreamState()

    const controller = createAbortController()
    const timeoutId = setTimeoutImpl(() => controller.abort(), chatTimeoutMs)

    try {
      await streamChatImpl({
        query: text,
        sessionId: currentSessionId.value,
        signal: controller.signal,
        onPayload: (data) => {
          isLoading.value = false
          applyPayload(assistantStreamState, data)
          assistantMessage.content = assistantStreamState.content
          assistantMessage.status = assistantStreamState.status
          persist()
          scroll()
        },
      })
    } catch (error) {
      logger.error('API Error:', error)
      assistantMessage.status = ''
      assistantMessage.content = formatErrorMessage(error, chatTimeoutMs)
      persist()
    } finally {
      clearTimeoutImpl(timeoutId)
      isLoading.value = false
      scroll()
    }
  }

  return {
    inputQuery,
    isLoading,
    sendQuery,
  }
}
