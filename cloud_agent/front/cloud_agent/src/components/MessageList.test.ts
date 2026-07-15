import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import MessageList from './MessageList.vue'

describe('MessageList', () => {
  it('renders the empty state and empty-actions slot when there are no messages', () => {
    const wrapper = mount(MessageList, {
      props: {
        messages: [],
        isLoading: false,
      },
      slots: {
        'empty-actions': '<button class="scenario-shortcut">Try scenario</button>',
      },
    })

    expect(wrapper.find('.empty-state').exists()).toBe(true)
    expect(wrapper.find('.scenario-shortcut').text()).toBe('Try scenario')
    expect(wrapper.findAll('.message-row')).toHaveLength(0)
  })

  it('renders user and assistant messages with markdown content and status', () => {
    const wrapper = mount(MessageList, {
      props: {
        messages: [
          { role: 'user', content: 'Hello **agent**' },
          { role: 'assistant', content: 'Here is `code`', status: 'Thinking' },
        ],
        isLoading: false,
      },
    })
    const rows = wrapper.findAll('.message-row')

    expect(rows).toHaveLength(2)
    expect(rows[0]!.classes()).toContain('user')
    expect(rows[1]!.classes()).toContain('assistant')
    expect(wrapper.find('.message-status').text()).toBe('Thinking')
    expect(wrapper.find('.message-content strong').text()).toBe('agent')
    expect(wrapper.find('.message-content code').text()).toBe('code')
  })

  it('renders a loading assistant row while the controller is waiting', () => {
    const wrapper = mount(MessageList, {
      props: {
        messages: [{ role: 'user', content: 'Need help' }],
        isLoading: true,
      },
    })
    const rows = wrapper.findAll('.message-row')

    expect(rows).toHaveLength(2)
    expect(rows[1]!.classes()).toContain('assistant')
    expect(wrapper.find('.message-bubble.loading').exists()).toBe(true)
  })

  it('exposes scrollToBottom for the chat controller', async () => {
    const wrapper = mount(MessageList, {
      props: {
        messages: [{ role: 'assistant', content: 'Long answer' }],
        isLoading: false,
      },
      attachTo: document.body,
    })
    const list = wrapper.find('.message-list').element as HTMLElement

    Object.defineProperty(list, 'scrollHeight', {
      configurable: true,
      value: 480,
    })

    await wrapper.vm.scrollToBottom()

    expect(list.scrollTop).toBe(480)
    wrapper.unmount()
  })
})
