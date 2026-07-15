import { ref } from 'vue'

export const CHAT_SESSIONS_STORAGE_KEY = 'cloud_agent_chat_sessions_v1'

const DEFAULT_SESSION = { id: 'session_default_1', name: '新对话' }

export function createMemoryStorage(initialItems = {}) {
  const items = new Map(Object.entries(initialItems))

  return {
    getItem(key) {
      return items.has(key) ? items.get(key) : null
    },
    setItem(key, value) {
      items.set(key, String(value))
    },
    removeItem(key) {
      items.delete(key)
    },
  }
}

export function useChatSessions({
  storage = resolveBrowserStorage(),
  storageKey = CHAT_SESSIONS_STORAGE_KEY,
  now = () => Date.now(),
} = {}) {
  const restoredState = loadPersistedState(storage, storageKey)
  const sessions = ref(restoredState.sessions)
  const currentSessionId = ref(restoredState.currentSessionId)
  const messagesBySession = restoredState.messagesBySession
  const messages = ref(messagesBySession[currentSessionId.value] || [])

  const persist = () => {
    messagesBySession[currentSessionId.value] = messages.value
    saveState(storage, storageKey, {
      currentSessionId: currentSessionId.value,
      sessions: sessions.value,
      messagesBySession,
    })
  }

  const createNewSession = () => {
    const id = createUniqueSessionId(now(), sessions.value)
    const session = { id, name: '新对话' }
    sessions.value.unshift(session)
    messagesBySession[id] = []
    currentSessionId.value = id
    messages.value = messagesBySession[id]
    persist()
    return session
  }

  const switchSession = (id) => {
    if (currentSessionId.value === id) return
    if (!sessions.value.some((session) => session.id === id)) return

    messagesBySession[currentSessionId.value] = messages.value
    currentSessionId.value = id
    messages.value = messagesBySession[id] || []
    messagesBySession[id] = messages.value
    persist()
  }

  const addMessage = (message) => {
    messages.value.push(message)
    persist()
    return message
  }

  return {
    sessions,
    currentSessionId,
    messages,
    createNewSession,
    switchSession,
    addMessage,
    persist,
  }
}

function resolveBrowserStorage() {
  return globalThis.localStorage || createMemoryStorage()
}

function createDefaultState() {
  return {
    currentSessionId: DEFAULT_SESSION.id,
    sessions: [{ ...DEFAULT_SESSION }],
    messagesBySession: {
      [DEFAULT_SESSION.id]: [],
    },
  }
}

function createUniqueSessionId(timestamp, sessions) {
  const baseId = `session_${timestamp}`
  if (!sessions.some((session) => session.id === baseId)) return baseId

  let suffix = 2
  while (sessions.some((session) => session.id === `${baseId}_${suffix}`)) {
    suffix += 1
  }
  return `${baseId}_${suffix}`
}

function loadPersistedState(storage, storageKey) {
  if (!storage) return createDefaultState()

  try {
    const raw = storage.getItem(storageKey)
    if (!raw) return createDefaultState()

    const parsed = JSON.parse(raw)
    return normalizeState(parsed)
  } catch {
    return createDefaultState()
  }
}

function normalizeState(value) {
  if (!value || typeof value !== 'object') return createDefaultState()

  const sessions = Array.isArray(value.sessions)
    ? value.sessions.filter(isSession)
    : []
  if (sessions.length === 0) return createDefaultState()

  const sessionIds = new Set(sessions.map((session) => session.id))
  const currentSessionId = sessionIds.has(value.currentSessionId)
    ? value.currentSessionId
    : sessions[0].id
  const messagesBySession = {}
  const rawMessagesBySession = value.messagesBySession && typeof value.messagesBySession === 'object'
    ? value.messagesBySession
    : {}

  for (const session of sessions) {
    const messages = rawMessagesBySession[session.id]
    messagesBySession[session.id] = Array.isArray(messages)
      ? messages.filter(isMessage)
      : []
  }

  return {
    currentSessionId,
    sessions,
    messagesBySession,
  }
}

function saveState(storage, storageKey, state) {
  if (!storage) return

  storage.setItem(storageKey, JSON.stringify(state))
}

function isSession(value) {
  return Boolean(
    value &&
    typeof value === 'object' &&
    typeof value.id === 'string' &&
    value.id.trim() &&
    typeof value.name === 'string' &&
    value.name.trim(),
  )
}

function isMessage(value) {
  return Boolean(
    value &&
    typeof value === 'object' &&
    (value.role === 'user' || value.role === 'assistant') &&
    typeof value.content === 'string' &&
    (value.status === undefined || typeof value.status === 'string'),
  )
}
