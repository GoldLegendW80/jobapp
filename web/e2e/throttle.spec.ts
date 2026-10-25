import { randomBytes } from "node:crypto";

import { expect, test, type BrowserContext, type Page } from "@playwright/test";

import {
  caddyUrl,
  clearAttempts,
  cliLink,
  makeShots,
  newContext,
  setPassword,
  watchErrors,
} from "./stack";

const shots = makeShots("identity-i7", "throttle");

/**
 * Sign in throttling against the real stack through Caddy: five wrong passwords, then the sixth
 * attempt is refused with 429, the server's message and a countdown on the disabled button. On a
 * scratch stack only (E2E_PASSWORD_THROTTLE=1), a second account also locks its password card
 * after five wrong current passwords.
 *
 * Locally, on a scratch Compose project (its own volumes, torn down afterwards):
 *   HTTP_PORT=8097 docker compose -p jobapp-i7 -f compose.yml -f compose.mac.yml up -d --build --wait
 *   docker compose -p jobapp-i7 -f compose.yml exec -T api alembic upgrade head
 *   E2E_CADDY_URL=http://127.0.0.1:8097 E2E_COMPOSE_PROJECT=jobapp-i7 E2E_PASSWORD_THROTTLE=1 \
 *     pnpm --dir web exec playwright test throttle.spec.ts
 *   docker compose -p jobapp-i7 -f compose.yml down -v
 *
 * On the Jetson, through the public URL, with the throwaway admin only, removed afterwards with
 * its sign in attempts (docs/runbooks/accounts.md, "Sign in throttling"):
 *   E2E_CADDY_URL=$(./deploy.sh url) E2E_ADMIN_VIA_DEPLOY=1 E2E_ADMIN_EMAIL=e2e-admin@example.org \
 *     E2E_SHOT_PREFIX=jetson- pnpm --dir web exec playwright test throttle.spec.ts
 */
test.describe.configure({ mode: "serial" });
test.skip(!caddyUrl, "set E2E_CADDY_URL with the Compose stack running");

const run = Date.now().toString(36);
const ADMIN = process.env.E2E_ADMIN_EMAIL ?? `e2e-admin-${run}@example.org`;
const SECOND = `e2e-card-${run}@example.org`;
// Fresh per run, never a common password, never logged.
const PASSWORD = `Pw ${randomBytes(12).toString("base64url")}`;
const WRONG = `Wrong ${randomBytes(12).toString("base64url")}`;
const INCORRECT = "Incorrect email or password.";
const LOGIN_429 = /^Too many sign in attempts\. Try again in 1[45] minutes\.$/;
const COUNTDOWN = /^Try again in 1[45]:\d\d$/;

let context: BrowserContext;
let page: Page;
let errors: string[];

test.beforeAll(async ({ browser }) => {
  context = await newContext(browser);
  page = await context.newPage();
  errors = watchErrors(page, [
    [429, /\/api\/auth\/(login|password)/],
    [400, /\/api\/auth\/password/],
  ]);
});

test.afterAll(async () => {
  await context.close();
  clearAttempts([ADMIN, SECOND]);
});

/** An account with PASSWORD from the CLI link (a reset on a rerun), then signed out. */
async function account(email: string) {
  const link = cliLink(email);
  await page.goto(link);
  await setPassword(
    page,
    PASSWORD,
    link.startsWith("/invite/") ? "E2E Admin" : undefined,
  );
}

/**
 * Drop the session cookie and land on the sign in page. The app is left for about:blank first:
 * cleared while still on /jobs, its list request would answer 401 and log a console error the
 * error watch rightly counts (issue #9). No page of the app is open while the cookie goes.
 */
async function signOut() {
  await page.goto("about:blank");
  await context.clearCookies();
  await page.goto("/login");
  await expect(page.getByRole("heading", { name: "Sign in" })).toBeVisible();
}

const submit = () => page.getByRole("button", { name: /Sign in|Try again/ });

/** Submit the sign in form; returns the status the api answered. */
async function tryLogin(email: string, password: string) {
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(password);
  const answer = page.waitForResponse((r) =>
    r.url().endsWith("/api/auth/login"),
  );
  await submit().click();
  return (await answer).status();
}

test("five wrong passwords, then the sixth attempt is throttled with a countdown", async () => {
  await account(ADMIN);
  await signOut();
  for (let i = 1; i <= 5; i += 1) {
    expect(await tryLogin(ADMIN, `${WRONG} ${i}`)).toBe(401);
    await expect(page.getByRole("alert")).toHaveText(INCORRECT);
    await expect(submit()).toHaveText("Sign in");
    await expect(submit()).toBeEnabled();
  }
  // The sixth is refused even with the right password, before it is checked.
  expect(await tryLogin(ADMIN, PASSWORD)).toBe(429);
  await expect(page.getByRole("alert")).toHaveText(LOGIN_429);
  await expect(submit()).toBeDisabled();
  await expect(submit()).toHaveText(COUNTDOWN);
  const first = await submit().textContent();
  await expect(submit()).not.toHaveText(first ?? "", { timeout: 5000 });
  await expect(page).toHaveURL(/\/login$/);
  await shots(page, "login-throttled");
  expect(errors).toEqual([]);
});

test("the password card waits out a 429 too", async () => {
  test.skip(
    process.env.E2E_PASSWORD_THROTTLE !== "1",
    "needs a second throwaway account: scratch stack only",
  );
  await account(SECOND);
  await page.goto("/account");
  const form = page.getByRole("form", { name: "Change password" });
  const button = form.getByRole("button", {
    name: /Change password|Try again/,
  });
  const fill = async (current: string) => {
    await form.getByLabel("Current password", { exact: true }).fill(current);
    await form
      .getByLabel("New password", { exact: true })
      .fill(`${PASSWORD} new`);
    await form
      .getByLabel("Confirm new password", { exact: true })
      .fill(`${PASSWORD} new`);
    const answer = page.waitForResponse((r) =>
      r.url().endsWith("/api/auth/password"),
    );
    await button.click();
    return (await answer).status();
  };
  for (let i = 1; i <= 5; i += 1) {
    expect(await fill(`${WRONG} ${i}`)).toBe(400);
    await expect(form).toContainText("The current password is incorrect.");
  }
  expect(await fill(PASSWORD)).toBe(429);
  await expect(form.getByRole("alert")).toHaveText(
    /^Too many attempts with a wrong current password\. Try again in 1[45] minutes\.$/,
  );
  await expect(button).toBeDisabled();
  await expect(button).toHaveText(COUNTDOWN);
  // From the top: a full page shot of a scrolled page paints the sticky header mid page.
  await page.evaluate(() => {
    window.scrollTo(0, 0);
  });
  await shots(page, "password-throttled");
  // Still signed in: a throttled change ends no session.
  await page.reload();
  await expect(
    page.getByRole("heading", { level: 1, name: "Account" }),
  ).toBeVisible();
  expect(errors).toEqual([]);
});
