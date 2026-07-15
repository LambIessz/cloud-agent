import { mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import App from './App.vue'
import { scenarioGroups } from './data/scenarios.js'

function createSseResponse(chunks: string[]) {
  const encoder = new TextEncoder()
  const body = new ReadableStream({
    start(controller) {
      for (const chunk of chunks) {
        controller.enqueue(encoder.encode(chunk))
      }
      controller.close()
    },
  })

  return new Response(body, { status: 200 })
}

function createStreamingFetch(contentForQuery: (query: string) => string) {
  const fetchMock = vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
    const body = JSON.parse(String(init?.body ?? '{}'))
    const content = contentForQuery(body.query)

    return createSseResponse([
      `data: ${JSON.stringify({ event_type: 'message_delta', content })}\n`,
      'data: {"event_type":"done"}\n',
    ])
  })

  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

async function waitUntil(assertion: () => void, timeoutMs = 1000) {
  const deadline = Date.now() + timeoutMs
  let lastError: unknown

  while (Date.now() < deadline) {
    try {
      assertion()
      return
    } catch (error) {
      lastError = error
      await new Promise((resolve) => window.setTimeout(resolve, 0))
    }
  }

  throw lastError
}

async function sendFromInput(wrapper: VueWrapper, value: string) {
  const textarea = wrapper.find('textarea')

  await textarea.setValue(value)
  textarea.element.dispatchEvent(
    new KeyboardEvent('keydown', {
      key: 'Enter',
      cancelable: true,
    }),
  )
  await wrapper.vm.$nextTick()
}

function latestRequestBody(fetchMock: ReturnType<typeof createStreamingFetch>) {
  const latestCall = fetchMock.mock.calls.at(-1)
  const init = latestCall?.[1] as RequestInit | undefined

  return JSON.parse(String(init?.body ?? '{}'))
}

describe('App wiring', () => {
  beforeEach(() => {
    window.localStorage.clear()
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    window.localStorage.clear()
    document.body.innerHTML = ''
  })

  it('wires ChatInput through the chat controller and renders a streamed reply', async () => {
    const fetchMock = createStreamingFetch(() => 'streamed assistant answer')
    const wrapper = mount(App, { attachTo: document.body })

    await sendFromInput(wrapper, '  Need ECS advice  ')
    await waitUntil(() => {
      expect(wrapper.text()).toContain('streamed assistant answer')
    })

    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(latestRequestBody(fetchMock)).toMatchObject({
      query: 'Need ECS advice',
      session_id: 'session_default_1',
    })
    expect(wrapper.find('.message-row.user').text()).toContain('Need ECS advice')
    expect((wrapper.find('textarea').element as HTMLTextAreaElement).value).toBe('')

    wrapper.unmount()
  })

  it('wires ScenarioGrid selections to the same chat controller', async () => {
    const firstScenario = scenarioGroups[0]!.items[0]!
    const fetchMock = createStreamingFetch(() => 'scenario assistant answer')
    const wrapper = mount(App, { attachTo: document.body })

    await wrapper.find('.scenario-item').trigger('click')
    await waitUntil(() => {
      expect(wrapper.text()).toContain('scenario assistant answer')
    })

    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(latestRequestBody(fetchMock).query).toBe(firstScenario.query)

    wrapper.unmount()
  })

  it('wires ChatSidebar session creation and switching to persisted messages', async () => {
    createStreamingFetch((query) => `response for ${query}`)
    const wrapper = mount(App, { attachTo: document.body })
    const firstQuery = 'first session question'
    const secondQuery = 'second session question'

    await sendFromInput(wrapper, firstQuery)
    await waitUntil(() => {
      expect(wrapper.text()).toContain(`response for ${firstQuery}`)
    })

    await wrapper.find('.sidebar-header button').trigger('click')
    await waitUntil(() => {
      expect(wrapper.find('.empty-state').exists()).toBe(true)
    })

    await sendFromInput(wrapper, secondQuery)
    await waitUntil(() => {
      expect(wrapper.text()).toContain(`response for ${secondQuery}`)
    })

    await wrapper.findAll('.session-item')[1]!.trigger('click')
    await waitUntil(() => {
      expect(wrapper.text()).toContain(firstQuery)
      expect(wrapper.text()).not.toContain(secondQuery)
    })

    await wrapper.findAll('.session-item')[0]!.trigger('click')
    await waitUntil(() => {
      expect(wrapper.text()).toContain(secondQuery)
      expect(wrapper.text()).not.toContain(firstQuery)
    })

    wrapper.unmount()
  })
})
