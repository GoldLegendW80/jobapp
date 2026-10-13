import { defineConfig, devices } from "@playwright/test";

/**
 * Every journey of the ingestion module in one Playwright run, against a running stack through
 * Caddy (no web server, no mocks): each web/e2e/ingestion-*.spec.ts, one after the other, each
 * with accounts of its own, so they do not depend on one another.
 *
 *   ingestion-g1   documents: add, paste, list, preview, delete, the 20 MiB route (task G1)
 *   ingestion-g2b  a PDF is read while the page watches: text, links, protected, scans (task G2b)
 *   ingestion-g7   a Word document and a LinkedIn export are read, a zip bomb is refused (task G7)
 *   ingestion-g10  a run of the chain: consent, start, done, reuse, the ceiling, cancel (task G10)
 *   ingestion-g11  the run in the page: the start dialog, the progress, Continue (task G11)
 *
 * A task of the module adds its spec as ingestion-<task>.spec.ts and it runs here: the CI
 * workflow names this config, not the specs, and .github/scripts/e2e_groups.py reads SPEC_PREFIX
 * below to know which files that covers.
 *
 *   E2E_CADDY_URL=http://127.0.0.1:8097 E2E_COMPOSE_PROJECT=jobapp-g1 \
 *     pnpm --dir web exec playwright test -c e2e/ingestion-all.config.ts
 */
const SPEC_PREFIX = "ingestion-";

if (!process.env.E2E_CADDY_URL)
  throw new Error("set E2E_CADDY_URL: this config runs against a live stack");

export default defineConfig({
  testDir: ".",
  testMatch: new RegExp(`/${SPEC_PREFIX}[\\w-]+\\.spec\\.ts$`),
  fullyParallel: false,
  workers: 1,
  // One retry in CI only: a slow runner should not turn main red; locally a failure shows at once.
  retries: process.env.CI ? 1 : 0,
  forbidOnly: true,
  timeout: 120_000,
  expect: { timeout: 15_000 },
  reporter: [["list"]],
  use: {
    baseURL: process.env.E2E_CADDY_URL,
    trace: "retain-on-failure",
    ...devices["Desktop Chrome"],
  },
});
