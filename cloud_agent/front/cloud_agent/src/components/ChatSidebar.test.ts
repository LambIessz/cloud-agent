import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import ChatSidebar from './ChatSidebar.vue'

const sessions = [
  { id: 'session-1', name: 'First session' },
  { id: 'session-2', name: 'Second session' },
]

describe('ChatSidebar', () => {
  it('marks the current session and emits session navigation actions', async () => {
    const wrapper = mount(ChatSidebar, {
      props: {
        sessions,
        currentSessionId: 'session-2',
      },
    })
    const items = wrapper.findAll('.session-item')

    expect(items).toHaveLength(2)
    expect(items[1]!.classes()).toContain('active')

    await wrapper.find('.sidebar-header button').trigger('click')
    await items[0]!.trigger('click')

    expect(wrapper.emitted('new-session')).toHaveLength(1)
    expect(wrapper.emitted('switch-session')?.[0]).toEqual(['session-1'])
  })
})
