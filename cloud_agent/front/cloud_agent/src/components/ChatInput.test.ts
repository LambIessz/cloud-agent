import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import ChatInput from './ChatInput.vue'

describe('ChatInput', () => {
  it('emits model updates from typed input', async () => {
    const wrapper = mount(ChatInput, {
      props: {
        modelValue: '',
        isLoading: false,
      },
    })

    await wrapper.find('textarea').setValue('How do I reduce cloud cost?')

    expect(wrapper.emitted('update:modelValue')?.at(-1)).toEqual([
      'How do I reduce cloud cost?',
    ])
  })

  it('sends the current query on Enter and prevents default submission', async () => {
    const wrapper = mount(ChatInput, {
      props: {
        modelValue: 'Check my latest bill',
        isLoading: false,
      },
    })
    const textarea = wrapper.find('textarea').element
    const event = new KeyboardEvent('keydown', {
      key: 'Enter',
      cancelable: true,
    })

    textarea.dispatchEvent(event)
    await wrapper.vm.$nextTick()

    expect(event.defaultPrevented).toBe(true)
    expect(wrapper.emitted('send')?.[0]).toEqual(['Check my latest bill'])
  })

  it('keeps Shift Enter as a newline-only interaction', async () => {
    const wrapper = mount(ChatInput, {
      props: {
        modelValue: 'Line one',
        isLoading: false,
      },
    })
    const textarea = wrapper.find('textarea').element
    const event = new KeyboardEvent('keydown', {
      key: 'Enter',
      shiftKey: true,
      cancelable: true,
    })

    textarea.dispatchEvent(event)
    await wrapper.vm.$nextTick()

    expect(event.defaultPrevented).toBe(false)
    expect(wrapper.emitted('send')).toBeUndefined()
  })

  it('does not send blank or loading queries', async () => {
    const blankWrapper = mount(ChatInput, {
      props: {
        modelValue: '   ',
        isLoading: false,
      },
    })
    const loadingWrapper = mount(ChatInput, {
      props: {
        modelValue: 'Still processing',
        isLoading: true,
      },
    })

    blankWrapper.find('textarea').element.dispatchEvent(
      new KeyboardEvent('keydown', { key: 'Enter', cancelable: true }),
    )
    loadingWrapper.find('textarea').element.dispatchEvent(
      new KeyboardEvent('keydown', { key: 'Enter', cancelable: true }),
    )
    await blankWrapper.vm.$nextTick()
    await loadingWrapper.vm.$nextTick()

    expect(blankWrapper.emitted('send')).toBeUndefined()
    expect(loadingWrapper.emitted('send')).toBeUndefined()
  })
})
