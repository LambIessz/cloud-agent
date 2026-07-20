export const SSE_SCHEMA_VERSION = '1.0'

const STREAM_START_LABELS = {
  cache: 'reading cache reply',
  fallback: 'starting agent stream',
  native: 'starting agent stream',
}

const AGENT_STEP_LABELS = {
  orchestrator: 'classifying request',
  _route_condition: 'selecting specialist',
  billing_agent: 'checking billing and resources',
  fallback_agent: 'confirming service boundary',
  finops_agent: 'analyzing cost optimization',
  finops_agent_trigger: 'analyzing cost optimization',
  product_agent: 'checking product catalog',
  promotion_agent: 'preparing promotion plan',
  recommendation_agent: 'generating recommendation',
  support_agent: 'investigating incident',
}

const ROUTE_DECISION_LABELS = {
  orchestrator: 'routing decision',
  _route_condition: 'routing decision',
}

const IGNORED_AGENT_STEPS = new Set(['LangGraph'])

export function createAssistantStreamState() {
  return {
    content: '',
    status: '',
    done: false,
    schemaVersion: '',
  }
}

export function formatAgentStep(step) {
  if (typeof step !== 'string') return ''

  const normalized = step.trim().replace(/\s+/g, ' ')
  if (!normalized) return ''
  if (IGNORED_AGENT_STEPS.has(normalized)) return ''

  if (AGENT_STEP_LABELS[normalized]) {
    return AGENT_STEP_LABELS[normalized]
  }

  const readableStep = normalized
    .replace(/^_+/, '')
    .replace(/[_-]+/g, ' ')
    .replace(/\b\w/g, (letter) => letter.toUpperCase())

  return `running ${readableStep}`
}

export function formatToolName(toolName) {
  if (typeof toolName !== 'string') return ''

  const normalized = toolName.trim().replace(/\s+/g, ' ')
  if (!normalized) return ''

  return normalized
    .replace(/^_+/, '')
    .replace(/[_-]+/g, ' ')
    .replace(/\b\w/g, (letter) => letter.toUpperCase())
}

function formatRouteDecision(payload) {
  if (!payload || typeof payload !== 'object') return ROUTE_DECISION_LABELS.orchestrator

  const routeTo = typeof payload.route_to === 'string' ? payload.route_to : ''
  if (!routeTo) {
    return ROUTE_DECISION_LABELS[payload.step] || ROUTE_DECISION_LABELS.orchestrator
  }

  const readableRoute = formatAgentStep(routeTo)
  return readableRoute || ROUTE_DECISION_LABELS[payload.step] || ROUTE_DECISION_LABELS.orchestrator
}

export function applySsePayload(state, payload) {
  if (!state || typeof state !== 'object') {
    throw new TypeError('stream state must be an object')
  }
  if (!payload || typeof payload !== 'object') {
    return state
  }

  const schemaVersion = typeof payload.schema_version === 'string' ? payload.schema_version.trim() : ''
  if (schemaVersion) {
    state.schemaVersion = schemaVersion
  }

  const eventType = typeof payload.event_type === 'string' ? payload.event_type.trim() : ''

  if (typeof payload.content === 'string') {
    state.content += payload.content
  }

  switch (eventType) {
    case 'stream_start':
      state.status = STREAM_START_LABELS[payload.stream_mode] || 'starting agent stream'
      break
    case 'route_decision':
      state.status = formatRouteDecision(payload)
      break
    case 'agent_step': {
      const status = formatAgentStep(payload.step)
      if (status) {
        state.status = status
      }
      break
    }
    case 'tool_call_start': {
      const toolName = formatToolName(payload.tool_name)
      state.status = toolName ? `calling ${toolName}` : 'calling tool'
      break
    }
    case 'tool_call_end': {
      const toolName = formatToolName(payload.tool_name)
      state.status = toolName ? `${toolName} completed` : 'tool call completed'
      break
    }
    case 'final':
      if (typeof payload.final === 'string' && state.content.length === 0) {
        state.content = payload.final
      } else if (typeof payload.content === 'string' && state.content.length === 0) {
        state.content = payload.content
      }
      state.done = true
      state.status = ''
      break
    case 'done':
      state.done = true
      state.status = ''
      break
    default:
      // Unknown future control events are ignored after legacy content merging.
      break
  }

  if (payload.done === true) {
    state.done = true
    state.status = ''
  }

  return state
}
