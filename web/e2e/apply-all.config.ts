import { defineConfig, devices } from "@playwright/test";

/**
 * Every journey of the apply module in one Playwright run, against a running stack through
 * Caddy plus the fake ATS forms server (tests/ats-forms-stub.compose.yml, service atsforms):
 * each web/e2e/apply-*.spec.ts, one after the other, each with accounts of its own. No journey
 * ever fills a real job site.
 *
 *   apply-a1   the test browser opens each fake form, fills it by hand code, submits, and the
 *              submission is listed (task A1)
 *   apply-a3   the Answers tab of the profile (task A3)
 *   apply-a7   the Apply section of a posting: apply, review, approve, applied (task A7)
 *
 * A task of the module adds its spec as apply-a<n>.spec.ts and it runs here: the local check
 * (ops/local-check.sh, group apply) names this config, not the specs, and
 * .github/scripts/e2e_groups.py reads SPEC_PREFIX below to know which files that covers.
 *
 *   ATS_FORMS_STUB_PORT=8100 HTTP_PORT=8095 docker compose -p jobapp-a1 -f compose.yml \
 *     -f tests/ats-stub.compose.yml -f tests/ats-forms-stub.compose.yml up -d --build --wait
 *   E2E_CADDY_URL=http://127.0.0.1:8095 E2E_ATS_FORMS_URL=http://127.0.0.1:8100 \
 *     E2E_COMPOSE_PROJECT=jobapp-a1 pnpm --dir web exec playwright test -c e2e/apply-all.config.ts
 */
const SPEC_PREFIX = "apply-";

if (!process.env.E2E_CADDY_URL)
  throw new Error("set E2E_CADDY_URL: this config runs against a live stack");
if (!process.env.E2E_ATS_FORMS_URL)
  throw new Error(
    "set E2E_ATS_FORMS_URL: the apply journeys need the fake forms of tests/ats-forms-stub.compose.yml",
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
