import { defineConfig, devices } from '@playwright/test'

// The first end-to-end flow (roadmap Phase 13c): a real browser against the real API, a real
// Temporal worker and a real database. Nothing is mocked; the case under review is seeded by
// scripts/seed_e2e.py, which has the actual workflow park it for review.
export default defineConfig({
  testDir: './e2e',
  timeout: 60_000,
  expect: { timeout: 15_000 },
  retries: 0,
  reporter: process.env.CI ? [['list'], ['html', { open: 'never' }]] : 'list',
  use: {
    baseURL: 'http://localhost:5173',
    trace: 'retain-on-failure',
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
  webServer: {
    command: 'npm run dev -- --port 5173 --strictPort',
    url: 'http://localhost:5173',
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
  },
})
