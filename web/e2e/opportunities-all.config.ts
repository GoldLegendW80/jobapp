import { defineConfig, devices } from "@playwright/test";

/**
 * Every journey of the opportunities module in one Playwright run, against a running stack
 * through Caddy plus the fake ATS forms server (tests/ats-forms-stub.compose.yml, service
 * atsforms), whose addresses the postings of a journey carry: each
 * web/e2e/opportunities-*.spec.ts, one after the other, each with accounts of its own. No
 * journey ever reads a real job site or platform.
 *
 *   opportunities-o2   the swipe deck: Like into the queue, Pass, undo, isolation (task O2)
 *   opportunities-o3   the pay and work mode a posting states, kept after an edit (task O3)
 *   opportunities-o4   seniority, industries, pay and preferences, the bonus a rescore adds (task O4)
 *   opportunities-o5   the first login wizard: a new member's seven steps, Back, a reload, the card (task O5)
 *   opportunities-o5b  the starter list of boards and sites on the wizard's summary (task O5b)
 *   opportunities-o5c  the same starter list in Search options, for an account that skipped the wizard (task O5c)
 *
 * A task of the module adds its spec as opportunities-o<n>.spec.ts and it runs here: the local
 * check (ops/local-check.sh, group opportunities) names this config, not the specs, and
 * .github/scripts/e2e_groups.py reads SPEC_PREFIX below to know which files that covers.
 *
 *   ATS_FORMS_STUB_PORT=8100 HTTP_PORT=8095 docker compose -p jobapp-o2 -f compose.yml \
 *     -f tests/ats-stub.compose.yml -f tests/ats-forms-stub.compose.yml up -d --build --wait
 *   E2E_CADDY_URL=http://127.0.0.1:8095 E2E_ATS_FORMS_URL=http://127.0.0.1:8100 \
 *     E2E_COMPOSE_PROJECT=jobapp-o2 pnpm --dir web exec playwright test -c e2e/opportunities-all.config.ts
 */
const SPEC_PREFIX = "opportunities-";

if (!process.env.E2E_CADDY_URL)
  throw new Error("set E2E_CADDY_URL: this config runs against a live stack");
if (!process.env.E2E_ATS_FORMS_URL)
  throw new Error(
    "set E2E_ATS_FORMS_URL: the deck journeys use the fake forms of tests/ats-forms-stub.compose.yml",
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
