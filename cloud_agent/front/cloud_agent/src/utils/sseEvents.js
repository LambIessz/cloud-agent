const STREAM_START_LABELS = {
  cache: '正在读取缓存回复',
  fallback: '正在启动智能体流程',
  native: '正在启动智能体流程',
}

const AGENT_STEP_LABELS = {
  orchestrator: '正在识别问题类型',
  _route_condition: '正在选择处理专家',
  billing_agent: '正在查询账单与资源',
  fallback_agent: '正在确认服务能力边界',
  finops_agent: '正在分析成本优化方案',
  finops_agent_trigger: '正在分析成本优化场景',
  product_agent: '正在查询云产品知识',
  promotion_agent: '正在准备推广方案',
  recommendation_agent: '正在生成选型建议',
  support_agent: '正在排查故障线索',
}

const IGNORED_AGENT_STEPS = new Set(['LangGraph'])

export function createAssistantStreamState() {
  return {
    content: '',
    status: '',
    done: false,
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

  return `正在执行 ${readableStep}`
}

export function applySsePayload(state, payload) {
  if (!state || typeof state !== 'object') {
    throw new TypeError('stream state must be an object')
  }
  if (!payload || typeof payload !== 'object') {
    return state
  }

  if (typeof payload.content === 'string') {
    state.content += payload.content
  }

  if (payload.event_type === 'stream_start') {
    state.status = STREAM_START_LABELS[payload.stream_mode] || '正在启动智能体流程'
  }

  if (payload.event_type === 'agent_step') {
    const status = formatAgentStep(payload.step)
    if (status) {
      state.status = status
    }
  }

  if (payload.event_type === 'done' || payload.done === true) {
    state.done = true
    state.status = ''
  }

  return state
}
