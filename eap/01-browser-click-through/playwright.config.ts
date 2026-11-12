import { defineConfig, devices } from '@playwright/test'

/**
 * LIUHAO AI OS — EAP browser click-through.
 *
 * Point this at the running single-port gateway with BASE_URL:
 *   BASE_URL=http://<host>:8080 npx playwright test
 *
 * The console is served by that gateway: root `/` returns index.html, API at
 * `/v1`. SPA routing is QUERY-STRING based (`/?p=<key>`), there is NO
 * `/console` path prefix.
 *
 * IMPORTANT: no globalSetup that requires seeded state. The human identity +
 * credential are seeded out-of-band on the host (see fixtures/seed-data.md),
 * so the test suite only assumes a reachable, seeded gateway. The login step
 * inside each spec performs the seed-dependent bootstrap at runtime.
 */
const BASE_URL = process.env.BASE_URL || 'http://localhost:8080'

export default defineConfig({
  testDir: './specs',
  timeout: 30_000,
  expect: {
    timeout: 10_000,
  },
  retries: 0,
  // reporters: json (machine-readable) + list (human-readable). Tuple form is required by Playwright.
  reporter: [['json'], ['list']],
  use: {
    baseURL: BASE_URL,
    headless: true,
    // Capture traces/screenshots on failure for host-side triage.
    screenshot: 'only-on-failure',
    trace: 'retain-on-failure',
  },
  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },
  ],
})
