import { defineConfig, devices } from '@playwright/test';
import path from 'path';

export default defineConfig({
  testDir: './e2e',
  timeout: 60000,
  expect: {
    timeout: 10000,
  },
  fullyParallel: false,
  reporter: 'list',
  use: {
    baseURL: 'http://127.0.0.1:7777',
    trace: 'on-first-retry',
  },
  webServer: {
    command: 'rm -f /tmp/nox_test_playwright.db* && python -c "from tests.fixtures.populate import populate_realistic_db; populate_realistic_db(\'/tmp/nox_test_playwright.db\')" && NOX_TEST_MODE=1 ../.venv/bin/nx run --test-db /tmp/nox_test_playwright.db',
    cwd: path.resolve('..'),
    url: 'http://127.0.0.1:7777/api/status',
    timeout: 25000,
    reuseExistingServer: true,
  },
  projects: [
    {
      name: 'chromium',
      use: {
        ...devices['Desktop Chrome'],
        viewport: { width: 1440, height: 900 },
      },
    },
  ],
});
