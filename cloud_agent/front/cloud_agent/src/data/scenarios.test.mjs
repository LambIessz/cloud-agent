import assert from 'node:assert/strict'
import test from 'node:test'

import { scenarioGroups } from './scenarios.js'

test('scenarioGroups defines the four default business scenario cards', () => {
  assert.equal(scenarioGroups.length, 4)
  assert.deepEqual(
    scenarioGroups.map((group) => group.title),
    ['产品咨询与推荐', '账单与实例查询', '资源优化与降本', '产品推广活动'],
  )

  for (const group of scenarioGroups) {
    assert.equal(typeof group.id, 'string')
    assert.notEqual(group.id.trim(), '')
    assert.equal(typeof group.icon, 'string')
    assert.equal(group.items.length, 2)

    for (const item of group.items) {
      assert.equal(typeof item.label, 'string')
      assert.equal(typeof item.query, 'string')
      assert.notEqual(item.label.trim(), '')
      assert.notEqual(item.query.trim(), '')
    }
  }
})

test('scenarioGroups keeps labels concise while preserving full query prompts', () => {
  const allItems = scenarioGroups.flatMap((group) => group.items)
  const queries = allItems.map((item) => item.query)

  assert.equal(new Set(queries).size, queries.length)
  assert.ok(queries.includes('我是Java接口服务+MySQL，8核16G够吗？推荐具体实例型号。'))
  assert.ok(queries.includes('获取近7天CPU/内存/带宽数据并做降本建议'))
  assert.ok(allItems.some((item) => item.label === 'Java服务+MySQL，推荐具体实例型号'))
})
