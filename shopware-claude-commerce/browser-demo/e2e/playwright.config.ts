// Copyright 2026 shopware AG
// SPDX-License-Identifier: MIT

/**
 * Playwright configuration for the in-browser demo.
 *
 * Runs against the local Node server (server/index.mjs): by default it starts `npm run dev`
 * on a dedicated port so a developer's own `npm run dev` on 4188 is left alone. Point
 * DEMO_E2E_BASE_URL at an already running server (dev or `npm start`) to reuse it.
 *
 * Environment:
 *   DEMO_E2E_BASE_URL   reuse a running server instead of starting one
 *   DEMO_E2E_PORT       port for the server this config starts (default 4189)
 *   DEMO_E2E_MODE       "dev" (default) or "static" (serves dist/site — run `npm run build` first)
 *   DEMO_E2E_NO_CHAT=1  skip the model-driven steps (no ANTHROPIC_API_KEY / no network)
 */
import { defineConfig, devices } from '@playwright/test';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const E2E_DIR = dirname(fileURLToPath(import.meta.url));
const DEMO_ROOT = resolve(E2E_DIR, '..');

const DEFAULT_PORT = 4189;
const port = Number(process.env.DEMO_E2E_PORT || DEFAULT_PORT);
const externalBaseUrl = process.env.DEMO_E2E_BASE_URL;
const baseURL = externalBaseUrl || `http://127.0.0.1:${port}`;
const mode = process.env.DEMO_E2E_MODE === 'static' ? 'static' : 'dev';

/**
 * One demo flow includes a cold boot (minutes on a cold cache) or several real model turns;
 * the individual waits are bounded inside the spec.
 */
const FLOW_TIMEOUT_MS = 12 * 60_000;
/** Server start: Vite's first optimize pass on the vendored apps can take a while. */
const SERVER_START_TIMEOUT_MS = 3 * 60_000;

export default defineConfig({
  testDir: E2E_DIR,
  testMatch: /.*\.spec\.ts$/,
  outputDir: resolve(E2E_DIR, 'test-results'),
  fullyParallel: false,
  workers: 1,
  retries: 0,
  timeout: FLOW_TIMEOUT_MS,
  // Long waits (cold boot, model turns) pass explicit timeouts; everything else fails fast.
  expect: { timeout: 60_000 },
  reporter: [['list'], ['html', { outputFolder: resolve(E2E_DIR, 'playwright-report'), open: 'never' }]],
  use: {
    ...devices['Desktop Chrome'],
    baseURL,
    viewport: { width: 1440, height: 900 },
    trace: 'retain-on-failure',
    screenshot: 'off',
    video: 'off',
    actionTimeout: 30_000,
    navigationTimeout: 60_000,
  },
  webServer: externalBaseUrl
    ? undefined
    : {
        command:
          mode === 'static'
            ? `node server/index.mjs --port ${port}`
            : `node server/index.mjs --dev --port ${port}`,
        cwd: DEMO_ROOT,
        url: `${baseURL}/api/anthropic/status`,
        reuseExistingServer: true,
        timeout: SERVER_START_TIMEOUT_MS,
        stdout: 'pipe',
        stderr: 'pipe',
      },
});
