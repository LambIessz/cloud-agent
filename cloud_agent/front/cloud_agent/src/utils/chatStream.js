export const DEFAULT_CHAT_ENDPOINT = '/api/chat'
export const DEFAULT_USER_ID = ''
export const DEFAULT_TENANT_ID = ''
export const CHAT_TIMEOUT_MS = 60000

function normalizeIdentityValue(value) {
  const trimmed = String(value ?? '').trim()
  return trimmed || ''
}

function buildChatPayload({ query, sessionId, userId, tenantId }) {
  const payload = {
    query,
    session_id: sessionId,
  }

  const normalizedUserId = normalizeIdentityValue(userId)
  const normalizedTenantId = normalizeIdentityValue(tenantId)
  if (normalizedUserId) {
    payload.user_id = normalizedUserId
  }
  if (normalizedTenantId) {
    payload.tenant_id = normalizedTenantId
  }
  return payload
}

function buildChatHeaders(userId, tenantId) {
  const headers = {
    Accept: 'text/event-stream',
    'Content-Type': 'application/json',
  }

  const normalizedUserId = normalizeIdentityValue(userId)
  const normalizedTenantId = normalizeIdentityValue(tenantId)
  if (normalizedUserId) {
    headers['X-User-Id'] = normalizedUserId
  }
  if (normalizedTenantId) {
    headers['X-Tenant-Id'] = normalizedTenantId
  }
  return headers
}

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
      headers: buildChatHeaders(userId, tenantId),
      body: JSON.stringify(
        buildChatPayload({
          query,
          sessionId,
          userId,
          tenantId,
        }),
      ),
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
    ? `请求超时：${Math.round(timeoutMs / 1000)} 秒内没有收到后端响应`
    : error instanceof Error ? error.message : String(error)

  return `请求失败：${detail}\n\n请检查后端 /readyz、Nginx /api/ 反向代理和 Docker 容器日志。`
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
