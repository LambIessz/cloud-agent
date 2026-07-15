import assert from 'node:assert/strict'
import test from 'node:test'
import { ref } from 'vue'

import { useChatController } from './useChatController.js'

function createHarness({
  streamChatImpl = async () => {},
  formatErrorMessage = (error, timeoutMs) => `ERR:${error.message}:${timeoutMs}`,
} = {}) {
  const messages = []
  const persisted = []
  const scrollCalls = []
  const clearedTimeouts = []
  const timeouts = []

  const controller = useChatController({
    currentSessionId: ref('session-123'),
    addMessage(message) {
      messages.push(message)
      return message
    },
    persist() {
      persisted.push(messages.map((message) => ({ ...message })))
    },
    scrollToBottom() {
      scrollCalls.push(messages.length)
    },
    streamChatImpl,
    formatErrorMessage,
    chatTimeoutMs: 1234,
    setTimeoutImpl(callback, delay) {
      timeouts.push({ callback, delay })
      return `timeout-${timeouts.length}`
    },
    clearTimeoutImpl(timeoutId) {
      clearedTimeouts.push(timeoutId)
    },
    logger: { error() {} },
  })

  return {
    controller,
    messages,
    persisted,
    scrollCalls,
    clearedTimeouts,
    timeouts,
  }
}

test('useChatController sends a trimmed query and streams assistant updates', async () => {
  const streamCalls = []
  const harness = createHarness({
    streamChatImpl: async (options) => {
      streamCalls.push(options)
      options.onPayload({ event_type: 'stream_start', stream_mode: 'native' })
      options.onPayload({ content: 'hello' })
      options.onPayload({ event_type: 'done' })
    },
  })
  harness.controller.inputQuery.value = '  hello  '

  await harness.controller.sendQuery(harness.controller.inputQuery.value)

  assert.equal(harness.controller.inputQuery.value, '')
  assert.equal(harness.controller.isLoading.value, false)
  assert.deepEqual(harness.messages, [
    { role: 'user', content: 'hello' },
    { role: 'assistant', content: 'hello', status: '' },
  ])
  assert.equal(streamCalls.length, 1)
  assert.equal(streamCalls[0].query, 'hello')
  assert.equal(streamCalls[0].sessionId, 'session-123')
  assert.equal(streamCalls[0].signal.aborted, false)
  assert.equal(harness.timeouts[0].delay, 1234)
  assert.deepEqual(harness.clearedTimeouts, ['timeout-1'])
  assert.equal(harness.persisted.length, 3)
  assert.ok(harness.scrollCalls.length >= 4)
})

test('useChatController ignores blank queries', async () => {
  let streamCallCount = 0
  const harness = createHarness({
    streamChatImpl: async () => {
      streamCallCount += 1
    },
  })

  await harness.controller.sendQuery('   ')

  assert.equal(streamCallCount, 0)
  assert.deepEqual(harness.messages, [])
  assert.equal(harness.controller.isLoading.value, false)
})

test('useChatController writes a readable assistant error message', async () => {
  const harness = createHarness({
    streamChatImpl: async () => {
      throw new Error('HTTP 503')
    },
  })

  await harness.controller.sendQuery('billing help')

  assert.equal(harness.controller.isLoading.value, false)
  assert.deepEqual(harness.messages, [
    { role: 'user', content: 'billing help' },
    { role: 'assistant', content: 'ERR:HTTP 503:1234', status: '' },
  ])
  assert.equal(harness.persisted.length, 1)
  assert.deepEqual(harness.clearedTimeouts, ['timeout-1'])
})

test('useChatController aborts the stream when the timeout fires', async () => {
  const streamSignals = []
  const harness = createHarness({
    streamChatImpl: async (options) => {
      streamSignals.push(options.signal)
      harness.timeouts[0].callback()
    },
  })

  await harness.controller.sendQuery('slow request')

  assert.equal(streamSignals.length, 1)
  assert.equal(streamSignals[0].aborted, true)
})
