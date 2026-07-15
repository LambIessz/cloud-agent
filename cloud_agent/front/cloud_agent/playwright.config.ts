import { defineConfig, devices } from '@playwright/test'

const frontendPort = Number(process.env.PLAYWRIGHT_FRONTEND_PORT || 15173)
const backendPort = Number(process.env.PLAYWRIGHT_BACKEND_PORT || 15100)
const frontendUrl = `http://127.0.0.1:${frontendPort}`
const backendUrl = `http://127.0.0.1:${backendPort}`

export default defineConfig({
  testDir: './src/smoke',
  testMatch: /browser-smoke\.spec\.ts/,
  outputDir: 'test-results',
  reporter: [
    ['list'],
    ['html', { outputFolder: 'playwright-report', open: 'never' }],
  ],
  timeout: 30_000,
  expect: {
    timeout: 8_000,
  },
  use: {
    ...devices['Desktop Chrome'],
    baseURL: frontendUrl,
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
  webServer: [
    {
      command: `node scripts/mock-chat-sse-server.mjs --port ${backendPort}`,
      url: `${backendUrl}/readyz`,
      reuseExistingServer: false,
      timeout: 30_000,
      stdout: 'pipe',
      stderr: 'pipe',
    },
    {
      command: `npm run dev -- --host 127.0.0.1 --port ${frontendPort}`,
      url: frontendUrl,
      reuseExistingServer: false,
      timeout: 60_000,
      stdout: 'pipe',
      stderr: 'pipe',
      env: {
        ...process.env,
        VITE_BACKEND_URL: backendUrl,
      },
    },
  ],
  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },
  ],
})
