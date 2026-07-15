export const DEFAULT_CHAT_ENDPOINT: string
export const DEFAULT_USER_ID: string
export const DEFAULT_TENANT_ID: string
export const CHAT_TIMEOUT_MS: number

export type ChatSsePayload = Record<string, unknown>

export interface BuildChatRequestOptions {
  query: string
  sessionId: string
  endpoint?: string
  signal?: AbortSignal
  userId?: string
  tenantId?: string
}

export interface ChatRequest {
  endpoint: string
  init: RequestInit & {
    method: 'POST'
    headers: Record<string, string>
    body: string
  }
}

export interface StreamChatOptions extends BuildChatRequestOptions {
  fetchImpl?: typeof fetch
  onPayload: (payload: ChatSsePayload) => void
}

export function buildChatRequest(options: BuildChatRequestOptions): ChatRequest
export function streamChat(options: StreamChatOptions): Promise<void>
export function formatChatErrorMessage(error: unknown, timeoutMs?: number): string
