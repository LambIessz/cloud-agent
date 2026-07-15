import assert from 'node:assert/strict'
import test from 'node:test'

import {
  CHAT_SESSIONS_STORAGE_KEY,
  createMemoryStorage,
  useChatSessions,
} from './useChatSessions.js'

test('useChatSessions starts with one default session when storage is empty', () => {
  const chat = useChatSessions({
    storage: createMemoryStorage(),
    now: () => 1000,
  })

  assert.equal(chat.currentSessionId.value, 'session_default_1')
  assert.deepEqual(chat.sessions.value, [
    { id: 'session_default_1', name: '新对话' },
  ])
  assert.deepEqual(chat.messages.value, [])
})

test('useChatSessions stores messages per session and restores them from storage', () => {
  const storage = createMemoryStorage()
  const chat = useChatSessions({
    storage,
    now: () => 1000,
  })

  chat.addMessage({ role: 'user', content: '第一条消息' })
  chat.addMessage({ role: 'assistant', content: '第一条回复', status: '完成' })
  chat.createNewSession()
  chat.addMessage({ role: 'user', content: '第二个会话' })

  assert.equal(chat.currentSessionId.value, 'session_1000')
  assert.deepEqual(chat.messages.value, [
    { role: 'user', content: '第二个会话' },
  ])

  chat.switchSession('session_default_1')
  assert.deepEqual(chat.messages.value, [
    { role: 'user', content: '第一条消息' },
    { role: 'assistant', content: '第一条回复', status: '完成' },
  ])

  const saved = JSON.parse(storage.getItem(CHAT_SESSIONS_STORAGE_KEY))
  assert.equal(saved.currentSessionId, 'session_default_1')
  assert.equal(saved.sessions.length, 2)
  assert.equal(saved.messagesBySession.session_1000[0].content, '第二个会话')

  const restored = useChatSessions({
    storage,
    now: () => 2000,
  })
  assert.equal(restored.currentSessionId.value, 'session_default_1')
  assert.deepEqual(restored.messages.value, [
    { role: 'user', content: '第一条消息' },
    { role: 'assistant', content: '第一条回复', status: '完成' },
  ])
})

test('useChatSessions falls back safely when persisted state is invalid', () => {
  const storage = createMemoryStorage({
    [CHAT_SESSIONS_STORAGE_KEY]: '{bad json',
  })

  const chat = useChatSessions({ storage })

  assert.equal(chat.currentSessionId.value, 'session_default_1')
  assert.deepEqual(chat.messages.value, [])
})

test('useChatSessions keeps helper methods stable for App.vue streaming updates', () => {
  const chat = useChatSessions({
    storage: createMemoryStorage(),
    now: () => 1234,
  })

  chat.addMessage({ role: 'user', content: 'hello' })
  const assistantMessage = chat.addMessage({ role: 'assistant', content: '', status: '' })
  assistantMessage.content = 'streamed'
  assistantMessage.status = '正在执行'
  chat.persist()

  assert.deepEqual(chat.messages.value, [
    { role: 'user', content: 'hello' },
    { role: 'assistant', content: 'streamed', status: '正在执行' },
  ])
})
