import { defineConfig, devices } from "@playwright/test";

/**
 * The whole identity surface in one Playwright run, against a running stack through Caddy (no
 * web server, no mocks): every journey spec once, in this order, one after the other, each a
 * project depending on the one before, so a failure stops the chain where it happened.
 *
 *   identity  login, the CLI link (invite), shell, sign out, invalid links, CLI reset
 *   invites   admin creates and revokes links, a friend accepts in another browser, member 404
 *   users     accounts table: disable signs out at once, enable, promote, demote
 *   account   rename, wrong current password, password change signs the other browser out
 *   profile   a made up profile through the CLI, every tab
 *   throttle  five wrong passwords, then 429 with a countdown (last: it locks its admin)
 *
 * Each spec deletes its own failed sign in attempts in its afterAll (stack.ts clearAttempts, which
 * runs `./deploy.sh admin unlock` on the Jetson), so the 20 failures per address and 15 minutes
 * are never reached by the run as a whole. tests/e2e_jetson.sh wraps this config for the Jetson:
 * Harald's account snapshot before and after, then every e2e row removed.
 *
 *   E2E_CADDY_URL=$(./deploy.sh url) E2E_ADMIN_VIA_DEPLOY=1 E2E_ADMIN_EMAIL=e2e-admin@example.org \
 *     E2E_EVIDENCE=identity pnpm --dir web exec playwright test -c e2e/identity-all.config.ts
 */
const SPECS = [
  "identity",
  "invites",
  "users",
  "account",
  "profile",
  "throttle",
] as const;

if (!process.env.E2E_CADDY_URL)
  throw new Error("set E2E_CADDY_URL: this config runs against a live stack");

export default defineConfig({
  testDir: ".",
  fullyParallel: false,
  workers: 1,
  retries: 0,
  forbidOnly: true,
  timeout: 120_000,
  expect: { timeout: 15_000 },
  reporter: [["list"]],
  use: {
    baseURL: process.env.E2E_CADDY_URL,
    trace: "retain-on-failure",
    ...devices["Desktop Chrome"],
  },
  projects: SPECS.map((spec, i) => ({
    name: spec,
    testMatch: new RegExp(`/${spec}\\.spec\\.ts$`),
    dependencies: i === 0 ? [] : [SPECS[i - 1] as string],
  })),
});
