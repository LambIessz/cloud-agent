import assert from 'node:assert/strict'
import test from 'node:test'

import {
  SSE_SCHEMA_VERSION,
  applySsePayload,
  createAssistantStreamState,
  formatAgentStep,
  formatToolName,
} from './sseEvents.js'

test('applySsePayload appends legacy and structured content deltas', () => {
  const state = createAssistantStreamState()

  applySsePayload(state, { content: 'hello' })
  applySsePayload(state, { event_type: 'message_delta', content: ' world' })

  assert.equal(state.content, 'hello world')
})

test('applySsePayload exposes route and tool status updates', () => {
  const routeState = createAssistantStreamState()
  applySsePayload(routeState, {
    schema_version: SSE_SCHEMA_VERSION,
    event_type: 'route_decision',
    route_to: 'billing_agent',
  })
  assert.equal(routeState.status, 'checking billing and resources')
  assert.equal(routeState.schemaVersion, SSE_SCHEMA_VERSION)

  const toolState = createAssistantStreamState()
  applySsePayload(toolState, { event_type: 'tool_call_start', tool_name: 'query_user_orders' })
  assert.equal(toolState.status, 'calling Query User Orders')
})

test('applySsePayload keeps unknown versioned events compatible', () => {
  const state = createAssistantStreamState()

  applySsePayload(state, {
    schema_version: SSE_SCHEMA_VERSION,
    event_type: 'future_control_event',
    content: 'hello',
  })

  assert.equal(state.schemaVersion, SSE_SCHEMA_VERSION)
  assert.equal(state.content, 'hello')
  assert.equal(state.status, '')
  assert.equal(state.done, false)
})

test('formatAgentStep maps internal workflow steps to readable labels', () => {
  assert.equal(formatAgentStep('orchestrator'), 'classifying request')
  assert.equal(formatAgentStep('_route_condition'), 'selecting specialist')
  assert.equal(formatAgentStep('fallback_agent'), 'confirming service boundary')
})

test('formatToolName softens machine names for display', () => {
  assert.equal(formatToolName('query_user_orders'), 'Query User Orders')
})

test('applySsePayload marks final and preserves streamed content', () => {
  const state = createAssistantStreamState()

  applySsePayload(state, { event_type: 'message_delta', content: 'hello' })
  applySsePayload(state, { event_type: 'final', final: 'hello' })

  assert.equal(state.content, 'hello')
  assert.equal(state.done, true)
  assert.equal(state.status, '')
})

test('applySsePayload marks done and clears transient status', () => {
  const state = createAssistantStreamState()

  applySsePayload(state, { event_type: 'agent_step', step: 'graph_query_agent' })
  applySsePayload(state, { event_type: 'done' })

  assert.equal(state.status, '')
  assert.equal(state.done, true)
})
