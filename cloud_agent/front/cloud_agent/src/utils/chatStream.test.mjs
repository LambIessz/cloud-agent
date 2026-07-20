import assert from 'node:assert/strict'
import test from 'node:test'

import {
  CHAT_TIMEOUT_MS,
  buildChatRequest,
  formatChatErrorMessage,
  streamChat,
} from './chatStream.js'
import { SSE_SCHEMA_VERSION } from './sseEvents.js'

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
    Accept: 'text/event-stream',
    'Content-Type': 'application/json',
  })
  assert.deepEqual(JSON.parse(request.init.body), {
    query: 'hello',
    session_id: 'session_1',
  })
})

test('buildChatRequest includes explicit identity headers when provided', () => {
  const request = buildChatRequest({
    query: 'hello',
    sessionId: 'session_1',
    userId: 'user_1002',
    tenantId: 'tenant_a',
  })

  assert.deepEqual(request.init.headers, {
    Accept: 'text/event-stream',
    'Content-Type': 'application/json',
    'X-User-Id': 'user_1002',
    'X-Tenant-Id': 'tenant_a',
  })
  assert.deepEqual(JSON.parse(request.init.body), {
    query: 'hello',
    user_id: 'user_1002',
    tenant_id: 'tenant_a',
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
        `data: {"schema_version":"${SSE_SCHEMA_VERSION}","event_type":"message_delta","content":"he`,
        'llo"}\n',
        `data: {"schema_version":"${SSE_SCHEMA_VERSION}","event_type":"final","final":"hello"}\n`,
        'data: [DONE]\n',
      ])
    },
    onPayload: (payload) => payloads.push(payload),
  })

  assert.equal(calls.length, 1)
  assert.equal(calls[0][0], '/api/chat')
  assert.deepEqual(payloads, [
    { schema_version: SSE_SCHEMA_VERSION, event_type: 'message_delta', content: 'hello' },
    { schema_version: SSE_SCHEMA_VERSION, event_type: 'final', final: 'hello' },
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

test('formatChatErrorMessage converts aborts and errors to user-facing copy', () => {
  assert.equal(CHAT_TIMEOUT_MS, 60000)

  const timeoutMessage = formatChatErrorMessage(new DOMException('', 'AbortError'))
  assert.match(timeoutMessage, /请求超时：60 秒内没有收到后端响应/)
  assert.match(timeoutMessage, /请检查后端 \/readyz、Nginx \/api\/ 反向代理和 Docker 容器日志。/)

  const errorMessage = formatChatErrorMessage(new Error('HTTP 500'))
  assert.match(errorMessage, /请求失败：HTTP 500/)
})
