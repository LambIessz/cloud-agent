import assert from 'node:assert/strict'
import test from 'node:test'

import { applySsePayload, createAssistantStreamState, formatAgentStep } from './sseEvents.js'

test('applySsePayload appends legacy and structured content deltas', () => {
  const state = createAssistantStreamState()

  applySsePayload(state, { content: 'hello' })
  applySsePayload(state, { event_type: 'message_delta', content: ' world' })

  assert.equal(state.content, 'hello world')
})

test('applySsePayload exposes user-facing agent step status without changing content', () => {
  const state = createAssistantStreamState()

  applySsePayload(state, { event_type: 'agent_step', step: 'billing_agent' })

  assert.equal(state.content, '')
  assert.equal(state.status, '正在查询账单与资源')
  assert.equal(state.done, false)
})

test('formatAgentStep maps internal workflow steps to readable labels', () => {
  assert.equal(formatAgentStep('orchestrator'), '正在识别问题类型')
  assert.equal(formatAgentStep('_route_condition'), '正在选择处理专家')
  assert.equal(formatAgentStep('fallback_agent'), '正在确认服务能力边界')
})

test('applySsePayload ignores framework-only agent steps', () => {
  const state = createAssistantStreamState()

  applySsePayload(state, { event_type: 'stream_start', stream_mode: 'native' })
  applySsePayload(state, { event_type: 'agent_step', step: 'LangGraph' })

  assert.equal(state.status, '正在启动智能体流程')
})

test('formatAgentStep softens unknown internal step names', () => {
  assert.equal(formatAgentStep('graph_query_agent'), '正在执行 Graph Query Agent')
})

test('applySsePayload marks done and clears transient status', () => {
  const state = createAssistantStreamState()

  applySsePayload(state, { event_type: 'agent_step', step: 'graph_query_agent' })
  applySsePayload(state, { event_type: 'done' })

  assert.equal(state.status, '')
  assert.equal(state.done, true)
})
