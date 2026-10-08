import { defineConfig, devices } from "@playwright/test";

const port = Number(process.env.E2E_PORT ?? 4173);
// E2E_SERVER=dev runs the tests against the Vite dev server instead of the production build.
const server =
  process.env.E2E_SERVER === "dev"
    ? `pnpm exec vite --host 127.0.0.1 --port ${port} --strictPort`
    : `pnpm build && pnpm exec vite preview --host 127.0.0.1 --port ${port} --strictPort`;

/**
 * End-to-end tests against the production build served by `vite preview`, or the dev server.
 * `/api` is mocked per test with route interception, except in `real-api.spec.ts`, which needs
 * E2E_REAL_API=up (or db-down) and an api listening where the proxy points
 * (VITE_API_PROXY_TARGET, default http://localhost:8000).
 */
export default defineConfig({
  testDir: "./e2e",
  fullyParallel: true,
  forbidOnly: Boolean(process.env.CI),
  retries: process.env.CI ? 1 : 0,
  reporter: [["list"]],
  use: {
    baseURL: `http://127.0.0.1:${port}`,
    trace: "retain-on-failure",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: {
    command: server,
    url: `http://127.0.0.1:${port}`,
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
  },
});
