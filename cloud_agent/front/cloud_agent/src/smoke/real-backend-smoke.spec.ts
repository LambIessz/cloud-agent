import { expect, test } from '@playwright/test'
import { mkdir, writeFile } from 'node:fs/promises'
import { resolve } from 'node:path'

const backendPort = Number(process.env.PLAYWRIGHT_REAL_BACKEND_PORT || 15200)
const backendUrl = `http://127.0.0.1:${backendPort}`
const diagnosticsDir = process.env.PLAYWRIGHT_REAL_BACKEND_DIAGNOSTICS_DIR || 'test-results-real-backend'
const diagnosticsPath = resolve(diagnosticsDir, 'real-backend-diagnostics.json')

async function readBackendJson(path: string) {
  try {
    const response = await fetch(`${backendUrl}${path}`)
    return {
      ok: response.ok,
      status: response.status,
      body: await response.json(),
    }
  } catch (error) {
    return {
      ok: false,
      status: 0,
      error: error instanceof Error ? error.message : String(error),
    }
  }
}

async function readBackendText(path: string) {
  try {
    const response = await fetch(`${backendUrl}${path}`)
    return {
      ok: response.ok,
      status: response.status,
      body: await response.text(),
    }
  } catch (error) {
    return {
      ok: false,
      status: 0,
      body: '',
      error: error instanceof Error ? error.message : String(error),
    }
  }
}

function selectMetrics(metricsText: string, patterns: RegExp[]) {
  return metricsText
    .split('\n')
    .filter((line) => line && !line.startsWith('#'))
    .filter((line) => patterns.some((pattern) => pattern.test(line)))
    .slice(0, 40)
}

test('loads the Vite app and completes chat through the real FastAPI backend', async ({ page }, testInfo) => {
  const query = '今天天气怎么样？'
  const consoleMessages: Array<{ type: string; text: string }> = []
  const pageErrors: string[] = []
  const requestFailures: Array<{ url: string; method: string; failure: string | null }> = []
  let requestId = ''
  let assistantText = ''
  let responseStatus = 0
  let responseContentType = ''
  let responseSchemaVersion = ''

  page.on('console', (message) => {
    if (message.type() !== 'error' && message.type() !== 'warning') {
      return
    }

    consoleMessages.push({
      type: message.type(),
      text: message.text().slice(0, 500),
    })
  })
  page.on('pageerror', (error) => {
    pageErrors.push(error.message.slice(0, 500))
  })
  page.on('requestfailed', (request) => {
    requestFailures.push({
      url: request.url(),
      method: request.method(),
      failure: request.failure()?.errorText || null,
    })
  })

  try {
    await page.goto('/')
    await expect(page.locator('.chat-container')).toBeVisible()

    const responsePromise = page.waitForResponse((response) =>
      response.url().includes('/api/chat') && response.status() === 200,
    )

    await page.locator('textarea').fill(query)
    await page.locator('textarea').press('Enter')
    const response = await responsePromise
    const responseHeaders = response.headers()
    requestId = response.headers()['x-request-id'] || ''
    responseStatus = response.status()
    responseContentType = responseHeaders['content-type'] || ''
    responseSchemaVersion = responseHeaders['x-sse-schema-version'] || ''

    expect(requestId).toMatch(/^req_[a-f0-9]{16}$/)
    expect(responseContentType).toContain('text/event-stream')
    expect(responseSchemaVersion).toBe('1.0')
    await expect(page.locator('.message-row.user')).toContainText(query)

    const assistantMessage = page.locator('.message-row.assistant').last()
    await expect(assistantMessage).toBeVisible()
    await expect(assistantMessage).not.toContainText('browser smoke reply')
    await expect(assistantMessage).toContainText('real backend smoke reply')
    await expect
      .poll(
        async () => {
          assistantText = (await assistantMessage.innerText()).trim()
          return assistantText.length
        },
        { timeout: 30_000 },
      )
      .toBeGreaterThan(20)

    assistantText = (await assistantMessage.innerText()).trim()
    expect(assistantText.length).toBeGreaterThan(20)
    await expect(page.locator('textarea')).toHaveValue('')
  } finally {
    const readyz = await readBackendJson('/readyz')
    const metrics = await readBackendText('/api/metrics')
    const requestMetrics = selectMetrics(metrics.body, [
      /^cloud_agent_requests_/,
      /^cloud_agent_request_duration_/,
      /^cloud_agent_route_/,
    ])
    const degradationMetrics = selectMetrics(metrics.body, [
      /^cloud_agent_degradation_/,
      /^cloud_agent_cache_/,
      /^cloud_agent_memory_/,
    ])
    const diagnostics = {
      requestId,
      query,
      pageUrl: page.url(),
      response: {
        status: responseStatus,
        contentType: responseContentType,
        schemaVersion: responseSchemaVersion,
      },
      readyz,
      requestMetrics,
      degradationMetrics,
      frontendDiagnostics: {
        consoleMessages: consoleMessages.slice(0, 20),
        pageErrors: pageErrors.slice(0, 20),
        requestFailures: requestFailures.slice(0, 20),
      },
      assistantTextPreview: assistantText.slice(0, 240),
    }
    const diagnosticsJson = `${JSON.stringify(diagnostics, null, 2)}\n`

    await mkdir(diagnosticsDir, { recursive: true })
    await writeFile(diagnosticsPath, diagnosticsJson, 'utf-8')
    await testInfo.attach('real-backend-diagnostics.json', {
      contentType: 'application/json',
      body: diagnosticsJson,
    })
  }
})
