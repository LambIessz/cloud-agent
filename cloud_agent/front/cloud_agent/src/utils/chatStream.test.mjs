import assert from 'node:assert/strict'
import test from 'node:test'

import {
  CHAT_TIMEOUT_MS,
  buildChatRequest,
  formatChatErrorMessage,
  streamChat,
} from './chatStream.js'

function createSseResponse(chunks, init = {}) {
  const encoder = new TextEncoder()
  const body = new ReadableStream({
    start(controller) {
      for (const chunk of chunks) {
        controller.enqueue(encoder.encode(chunk))
      }
      controller.close()
    },
  })

  return new Response(body, { status: 200, ...init })
}

test('buildChatRequest creates the canonical chat POST request', () => {
  const request = buildChatRequest({
    query: 'hello',
    sessionId: 'session_1',
  })

  assert.equal(request.endpoint, '/api/chat')
  assert.equal(request.init.method, 'POST')
  assert.deepEqual(request.init.headers, {
    'Content-Type': 'application/json',
    'X-User-Id': 'user_1001',
    'X-Tenant-Id': 'default_tenant',
  })
  assert.deepEqual(JSON.parse(request.init.body), {
    query: 'hello',
    user_id: 'user_1001',
    tenant_id: 'default_tenant',
    session_id: 'session_1',
  })
})

test('streamChat parses SSE data lines even when JSON arrives across chunks', async () => {
  const calls = []
  const payloads = []

  await streamChat({
    query: 'hello',
    sessionId: 'session_1',
    fetchImpl: async (...args) => {
      calls.push(args)
      return createSseResponse([
        'data: {"event_type":"message_delta","content":"he',
        'llo"}\n',
        'data: {"event_type":"done"}\n',
        'data: [DONE]\n',
      ])
    },
    onPayload: (payload) => payloads.push(payload),
  })

  assert.equal(calls.length, 1)
  assert.equal(calls[0][0], '/api/chat')
  assert.deepEqual(payloads, [
    { event_type: 'message_delta', content: 'hello' },
    { event_type: 'done' },
  ])
})

test('streamChat throws a readable HTTP error for non-OK responses', async () => {
  await assert.rejects(
    () =>
      streamChat({
        query: 'hello',
        sessionId: 'session_1',
        fetchImpl: async () =>
          new Response('backend unavailable', {
            status: 503,
            statusText: 'Service Unavailable',
          }),
        onPayload: () => {},
      }),
    /HTTP 503: backend unavailable/,
  )
})

test('formatChatErrorMessage converts aborts and errors to user-facing Chinese copy', () => {
  assert.equal(CHAT_TIMEOUT_MS, 60000)

  const timeoutMessage = formatChatErrorMessage(new DOMException('', 'AbortError'))
  assert.match(timeoutMessage, /请求超时：60 秒内没有收到后端响应/)
  assert.match(timeoutMessage, /请检查后端 \/readyz、Nginx \/api\/ 反向代理和 Docker 容器日志。/)

  const errorMessage = formatChatErrorMessage(new Error('HTTP 500'))
  assert.match(errorMessage, /请求失败：HTTP 500/)
})
