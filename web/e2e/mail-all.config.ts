import { defineConfig, devices } from "@playwright/test";

/**
 * Every journey of the mail module in one Playwright run, against a running stack through
 * Caddy plus the fake Google (tests/gmail-stub.compose.yml, service gmailstub): each
 * web/e2e/mail-*.spec.ts, one after the other, each with accounts of its own. No journey ever
 * reaches a real Google account.
 *
 *   mail-m1   the stack starts with the fake Google healthy, the browser reaches its consent
 *             page and lands on the redirect address with a code (task M1)
 *
 * A task of the module adds its spec as mail-m<n>.spec.ts and it runs here: the local check
 * (ops/local-check.sh, group mail) names this config, not the specs, and
 * .github/scripts/e2e_groups.py reads SPEC_PREFIX below to know which files that covers.
 *
 *   GMAIL_STUB_PORT=8098 docker compose -p jobapp-m1 -f compose.yml -f tests/ats-stub.compose.yml \
 *     -f tests/gmail-stub.compose.yml up -d --build --wait
 *   E2E_CADDY_URL=http://127.0.0.1:8097 E2E_GMAIL_STUB_URL=http://127.0.0.1:8098 \
 *     E2E_COMPOSE_PROJECT=jobapp-m1 pnpm --dir web exec playwright test -c e2e/mail-all.config.ts
 */
const SPEC_PREFIX = "mail-";

if (!process.env.E2E_CADDY_URL)
  throw new Error("set E2E_CADDY_URL: this config runs against a live stack");
if (!process.env.E2E_GMAIL_STUB_URL)
  throw new Error(
    "set E2E_GMAIL_STUB_URL: the mail journeys need the fake Google of tests/gmail-stub.compose.yml",
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
