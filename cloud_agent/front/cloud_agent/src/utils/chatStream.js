export const DEFAULT_CHAT_ENDPOINT = '/api/chat'
export const DEFAULT_USER_ID = 'user_1001'
export const DEFAULT_TENANT_ID = 'default_tenant'
export const CHAT_TIMEOUT_MS = 60000

export function buildChatRequest({
  query,
  sessionId,
  endpoint = DEFAULT_CHAT_ENDPOINT,
  signal,
  userId = DEFAULT_USER_ID,
  tenantId = DEFAULT_TENANT_ID,
}) {
  return {
    endpoint,
    init: {
      method: 'POST',
      signal,
      headers: {
        'Content-Type': 'application/json',
        'X-User-Id': userId,
        'X-Tenant-Id': tenantId,
      },
      body: JSON.stringify({
        query,
        user_id: userId,
        tenant_id: tenantId,
        session_id: sessionId,
      }),
    },
  }
}

export async function streamChat({
  query,
  sessionId,
  signal,
  endpoint = DEFAULT_CHAT_ENDPOINT,
  fetchImpl = globalThis.fetch,
  onPayload,
  userId = DEFAULT_USER_ID,
  tenantId = DEFAULT_TENANT_ID,
}) {
  if (typeof fetchImpl !== 'function') {
    throw new TypeError('fetch implementation is required')
  }
  if (typeof onPayload !== 'function') {
    throw new TypeError('onPayload callback is required')
  }

  const request = buildChatRequest({
    query,
    sessionId,
    endpoint,
    signal,
    userId,
    tenantId,
  })
  const response = await fetchImpl(request.endpoint, request.init)

  if (!response.ok) {
    const errorBody = await response.text().catch(() => '')
    const detail = errorBody ? `: ${errorBody.slice(0, 180)}` : ''
    throw new Error(`HTTP ${response.status}${detail}`)
  }

  const reader = response.body?.getReader()
  if (!reader) return

  const decoder = new TextDecoder('utf-8')
  let buffer = ''

  while (true) {
    const { done, value } = await reader.read()
    if (done) break

    buffer += decoder.decode(value, { stream: true })
    buffer = parseSseBuffer(buffer, onPayload)
  }

  buffer += decoder.decode()
  if (buffer) {
    parseSseBuffer(`${buffer}\n`, onPayload)
  }
}

export function formatChatErrorMessage(error, timeoutMs = CHAT_TIMEOUT_MS) {
  const detail = error instanceof DOMException && error.name === 'AbortError'
    ? `请求超时：${timeoutMs / 1000} 秒内没有收到后端响应`
    : error instanceof Error ? error.message : String(error)

  return `❌ 请求失败：${detail}\n\n请检查后端 /readyz、Nginx /api/ 反向代理和 Docker 容器日志。`
}

function parseSseBuffer(buffer, onPayload) {
  const lines = buffer.split('\n')
  const remainder = lines.pop() || ''

  for (const line of lines) {
    const trimmedLine = line.trimEnd()
    if (!trimmedLine.startsWith('data: ')) continue

    const dataStr = trimmedLine.slice(6).trim()
    if (!dataStr || dataStr === '[DONE]') continue

    onPayload(JSON.parse(dataStr))
  }

  return remainder
}
