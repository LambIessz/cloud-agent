import assert from 'node:assert/strict'
import test from 'node:test'

import { renderMarkdown } from './markdown.js'

test('renderMarkdown keeps basic markdown formatting', () => {
  const html = renderMarkdown('Use **ECS** for compute')

  assert.match(html, /<strong>ECS<\/strong>/)
})

test('renderMarkdown escapes raw html instead of executing it', () => {
  const html = renderMarkdown('<img src=x onerror=alert(1)><script>alert(1)</script>')

  assert.doesNotMatch(html, /<img/i)
  assert.doesNotMatch(html, /<script/i)
  assert.match(html, /&lt;img/)
  assert.match(html, /&lt;script/)
})

test('renderMarkdown drops unsafe link and image urls', () => {
  const html = renderMarkdown(
    '[bad](javascript:alert(1)) [ok](https://example.com/docs) ![x](data:image/svg+xml;base64,PHN2ZyBvbmxvYWQ9YWxlcnQoMSk+)',
  )

  assert.doesNotMatch(html, /javascript:/i)
  assert.doesNotMatch(html, /data:image/i)
  assert.doesNotMatch(html, /<img/i)
  assert.match(html, /\bbad\b/)
  assert.match(html, /href="https:\/\/example.com\/docs"/)
})
