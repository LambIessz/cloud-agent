import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

import { defineConfig, devices } from '@playwright/test'

const configDir = dirname(fileURLToPath(import.meta.url))
const repoRoot = resolve(configDir, '../../..')
const backendCwd = resolve(configDir, '../../app')
const frontendPort = Number(process.env.PLAYWRIGHT_REAL_FRONTEND_PORT || 15273)
const backendPort = Number(process.env.PLAYWRIGHT_REAL_BACKEND_PORT || 15200)
const frontendUrl = `http://127.0.0.1:${frontendPort}`
const backendUrl = `http://127.0.0.1:${backendPort}`

export default defineConfig({
  testDir: './src/smoke',
  testMatch: 'real-backend-smoke.spec.ts',
  outputDir: 'test-results-real-backend',
  reporter: [
    ['list'],
    ['html', { outputFolder: 'playwright-report-real-backend', open: 'never' }],
  ],
  timeout: 90_000,
  expect: {
    timeout: 15_000,
  },
  use: {
    ...devices['Desktop Chrome'],
    baseURL: frontendUrl,
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
  webServer: [
    {
      command: `python -X utf8 -m uvicorn app_main:app --host 127.0.0.1 --port ${backendPort}`,
      cwd: backendCwd,
      url: `${backendUrl}/readyz`,
      reuseExistingServer: false,
      timeout: 90_000,
      stdout: 'pipe',
      stderr: 'pipe',
      env: {
        ...process.env,
        PYTHONIOENCODING: 'utf-8',
        PYTHONUTF8: '1',
        HF_ENDPOINT: 'https://hf-mirror.com',
        HF_HUB_DISABLE_SYMLINKS_WARNING: '1',
        CLOUD_AGENT_LLM_PRICING_CONFIG: resolve(
          repoRoot,
          'ops/prometheus/llm_pricing.example.yml',
        ),
        DEEPSEEK_API_KEY: 'ci-placeholder',
        BASE_URL: 'http://127.0.0.1:9/v1',
        MODEL: 'deepseek-chat',
        REDIS_URL: 'redis://127.0.0.1:6379',
        CLOUD_AGENT_SMOKE_FAKE_GRAPH: 'true',
        CLOUD_AGENT_AUTH_MODE: 'local',
        CLOUD_AGENT_CORS_ORIGINS: frontendUrl,
        CLOUD_AGENT_SEMANTIC_CACHE_ENABLED: 'false',
        CLOUD_AGENT_LONG_TERM_MEMORY_ENABLED: 'false',
        CLOUD_AGENT_VECTOR_SEARCH_ENABLED: 'false',
        CLOUD_AGENT_KNOWLEDGE_GRAPH_ENABLED: 'false',
        CLOUD_AGENT_BACKGROUND_EXTRACT_ENABLED: 'false',
        CLOUD_AGENT_SEMANTIC_CACHE_WRITE_ENABLED: 'false',
      },
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
