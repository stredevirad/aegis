import { defineConfig, devices } from "@playwright/test";

// Browser tests against a REAL backend: the dev stack (backend/scripts/dev-stack.sh + `npm run dev`)
// by default, or the one-command compose stack with E2E_BASE_URL=http://127.0.0.1:8080.
// E2E_RECORD=1 records a video of every test (used to produce the README GIF).
const baseURL = process.env.E2E_BASE_URL ?? "http://127.0.0.1:5173";
const record = process.env.E2E_RECORD === "1";

export default defineConfig({
  testDir: "./e2e",
  outputDir: "./test-results",
  // The tests share one organization and the investigations queue: run them one at a time.
  workers: 1,
  fullyParallel: false,
  forbidOnly: Boolean(process.env.CI),
  retries: 0,
  timeout: 90_000,
  expect: { timeout: 20_000 },
  reporter: process.env.CI ? [["list"], ["html", { open: "never" }]] : [["list"]],
  use: {
    ...devices["Desktop Chrome"],
    baseURL,
    viewport: { width: 1366, height: 900 },
    video: record ? { mode: "on", size: { width: 1366, height: 900 } } : "retain-on-failure",
    // Recording for the README GIF: slow down so a viewer can follow the clicks.
    launchOptions: record ? { slowMo: 400 } : {},
    screenshot: "only-on-failure",
    trace: "retain-on-failure",
  },
});
