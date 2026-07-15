import { expect, test } from '@playwright/test'

test('loads the Vite app and completes chat through the /api/chat proxy', async ({ page }) => {
  const query = 'browser smoke proxy question'

  await page.goto('/')
  await expect(page.locator('.chat-container')).toBeVisible()

  const responsePromise = page.waitForResponse((response) =>
    response.url().includes('/api/chat') && response.status() === 200,
  )

  await page.locator('textarea').fill(query)
  await page.locator('textarea').press('Enter')
  await responsePromise

  await expect(page.locator('.message-row.user')).toContainText(query)
  await expect(page.locator('.message-row.assistant')).toContainText(
    `browser smoke reply: ${query}`,
  )
  await expect(page.locator('textarea')).toHaveValue('')
})
