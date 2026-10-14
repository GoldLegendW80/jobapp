import { defineConfig, devices } from "@playwright/test";

/**
 * Every journey of the documents module in one Playwright run, against a running stack through
 * Caddy (its worker renders through the `renderer` service of compose.yml): each
 * web/e2e/documents-*.spec.ts, one after the other, each with accounts of its own and only
 * invented data (web/e2e/documents-helpers.ts).
 *
 *   documents-d6   a hand written CV draft saved through the api, rendered to a one page PDF,
 *                  switched to another template, and the posting moved to cv_ready (task D6)
 *
 * A task of the module adds its spec as documents-d<n>.spec.ts and it runs here: the local
 * check (ops/local-check.sh, group documents) names this config, not the specs, and
 * .github/scripts/e2e_groups.py reads SPEC_PREFIX below to know which files that covers.
 *
 *   HTTP_PORT=8099 docker compose -p jobapp-d6 -f compose.yml -f compose.mac.yml up -d --wait
 *   docker compose -p jobapp-d6 -f compose.yml exec -T api alembic upgrade head
 *   E2E_CADDY_URL=http://127.0.0.1:8099 E2E_COMPOSE_PROJECT=jobapp-d6 \
 *     pnpm --dir web exec playwright test -c e2e/documents-all.config.ts
 *   docker compose -p jobapp-d6 -f compose.yml down -v
 */
const SPEC_PREFIX = "documents-";

if (!process.env.E2E_CADDY_URL)
  throw new Error("set E2E_CADDY_URL: this config runs against a live stack");
if (!process.env.E2E_COMPOSE_PROJECT)
  throw new Error(
    "set E2E_COMPOSE_PROJECT: the documents journeys seed their documents in the api container",
  );

export default defineConfig({
  testDir: ".",
  testMatch: new RegExp(`/${SPEC_PREFIX}[\\w-]+\\.spec\\.ts$`),
  fullyParallel: false,
  workers: 1,
  retries: process.env.CI ? 1 : 0,
  forbidOnly: true,
  timeout: 180_000,
  expect: { timeout: 15_000 },
  reporter: [["list"]],
  use: {
    baseURL: process.env.E2E_CADDY_URL,
    trace: "retain-on-failure",
    ...devices["Desktop Chrome"],
  },
});
