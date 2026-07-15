import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import ScenarioGrid from './ScenarioGrid.vue'

const scenarios = [
  {
    id: 'product',
    title: 'Product consulting',
    icon: 'Monitor' as const,
    items: [
      {
        label: 'Recommend an ECS instance',
        query: 'Recommend an ECS instance for Java and MySQL',
      },
      {
        label: 'Explain ECS basics',
        query: 'What are the basic ECS attributes?',
      },
    ],
  },
  {
    id: 'billing',
    title: 'Billing',
    icon: 'List' as const,
    items: [
      {
        label: 'Check orders',
        query: 'Show my recent order records',
      },
    ],
  },
]

describe('ScenarioGrid', () => {
  it('renders scenario choices and emits the selected query', async () => {
    const wrapper = mount(ScenarioGrid, {
      props: {
        scenarios,
      },
    })
    const items = wrapper.findAll('.scenario-item')

    expect(wrapper.findAll('.scenario-card')).toHaveLength(2)
    expect(items).toHaveLength(3)

    await items[0]!.trigger('click')

    expect(wrapper.emitted('select-query')?.[0]).toEqual([
      'Recommend an ECS instance for Java and MySQL',
    ])
  })
})
