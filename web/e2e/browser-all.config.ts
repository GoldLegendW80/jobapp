import { defineConfig, devices } from "@playwright/test";

/**
 * Every journey of the browser module in one Playwright run, against a running stack through
 * Caddy with its browser (compose profile `browser`) and the static pages of
 * tests/browser_pages/ (tests/browser-pages.compose.yml, service pages): each
 * web/e2e/browser-*.spec.ts, one after the other, on the one browser of the stack, each with
 * accounts of its own. No journey ever opens a real site.
 *
 *   browser-b3   leases and the queue of the api (task B3)
 *   browser-b4   the live view of an assistant's lease: Take over and Give back (task B4)
 *   browser-b7   Sign in to a site, the profile kept across leases and a restart, Forget all
 *                logins (task B7)
 *   browser-b12  the sign in hand off: the banner, the live view at the person's keyboard,
 *                I'm signed in, Cancel, and nobody watching (task B12b)
 *
 * A task of the module adds its spec as browser-b<n>.spec.ts and it runs here: the local check
 * (ops/local-check.sh, group browser) names this config, not the specs, and
 * .github/scripts/e2e_groups.py reads SPEC_PREFIX below to know which files that covers.
 *
 *   COMPOSE_PROFILES=browser HTTP_PORT=8097 docker compose -p jobapp-b7 -f compose.yml \
 *     -f tests/browser-pages.compose.yml up -d --build --wait
 *   docker compose -p jobapp-b7 -f compose.yml exec -T api alembic upgrade head
 *   E2E_CADDY_URL=http://127.0.0.1:8097 E2E_COMPOSE_PROJECT=jobapp-b7 \
 *     pnpm --dir web exec playwright test -c e2e/browser-all.config.ts
 */
const SPEC_PREFIX = "browser-";

if (!process.env.E2E_CADDY_URL)
  throw new Error("set E2E_CADDY_URL: this config runs against a live stack");
if (!process.env.E2E_COMPOSE_PROJECT)
  throw new Error(
    "set E2E_COMPOSE_PROJECT: the browser journeys reach browserd inside that stack",
  );

export default defineConfig({
  testDir: ".",
  testMatch: new RegExp(`/${SPEC_PREFIX}[\\w-]+\\.spec\\.ts$`),
  fullyParallel: false,
  workers: 1,
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
