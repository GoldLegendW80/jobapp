import { randomBytes } from "node:crypto";

import { expect, test, type BrowserContext, type Page } from "@playwright/test";

import {
  caddyUrl,
  clearAttempts,
  cliLink,
  makeShots,
  menuButton,
  newContext,
  setPassword,
  signIn,
  watchErrors,
} from "./stack";

const shots = makeShots("identity-i6", "account");

/**
 * The account page against the real stack through Caddy: no mocks. Rename yourself, a wrong
 * current password, a confirmation mismatch, then a password change in one browser that signs
 * the other browser out while this one stays signed in.
 *
 * Locally, on a scratch Compose project (its own volumes, torn down afterwards):
 *   HTTP_PORT=8096 docker compose -p jobapp-i6 -f compose.yml -f compose.mac.yml up -d --build --wait
 *   docker compose -p jobapp-i6 -f compose.yml exec -T api alembic upgrade head
 *   E2E_CADDY_URL=http://127.0.0.1:8096 E2E_COMPOSE_PROJECT=jobapp-i6 \
 *     pnpm --dir web exec playwright test account.spec.ts
 *   docker compose -p jobapp-i6 -f compose.yml down -v
 *
 * On the Jetson, through the public URL, with the throwaway admin removed afterwards (README):
 *   E2E_CADDY_URL=$(./deploy.sh url) E2E_ADMIN_VIA_DEPLOY=1 E2E_ADMIN_EMAIL=e2e-admin@example.org \
 *     E2E_SHOT_PREFIX=jetson- pnpm --dir web exec playwright test account.spec.ts
 */
test.describe.configure({ mode: "serial" });
test.skip(!caddyUrl, "set E2E_CADDY_URL with the Compose stack running");

const run = Date.now().toString(36);
const ADMIN = process.env.E2E_ADMIN_EMAIL ?? `e2e-admin-${run}@example.org`;
const NAME = "E2E Admin";
const RENAMED = "E2E Zoë Ångström";
// Fresh per run, never a common password, never logged.
const FIRST_PASSWORD = `Pw ${randomBytes(12).toString("base64url")}`;
const NEW_PASSWORD = `Pw ${randomBytes(12).toString("base64url")}`;
const CHANGED = "Password changed. Other devices are signed out.";

let one: BrowserContext;
let here: Page;
let hereErrors: string[];
let two: BrowserContext;
let there: Page;
let thereErrors: string[];

const passwordForm = (page: Page) =>
  page.getByRole("form", { name: "Change password" });
const nameForm = (page: Page) =>
  page.getByRole("form", { name: "Profile name" });

test.beforeAll(async ({ browser }) => {
  one = await newContext(browser);
  here = await one.newPage();
  hereErrors = watchErrors(here, [[400, /\/api\/auth\/password/]]);
  two = await newContext(browser);
  there = await two.newPage();
  thereErrors = watchErrors(there);
});

test.afterAll(async () => {
  await one.close();
  await two.close();
  clearAttempts([ADMIN]);
});

test("the admin is signed in on two browsers", async () => {
  const link = cliLink(ADMIN);
  await here.goto(link);
  // A first run sets up the account; a rerun for an existing admin gets a reset link.
  const fresh = link.startsWith("/invite/");
  await setPassword(here, FIRST_PASSWORD, fresh ? NAME : undefined);
  if (!fresh) {
    // Bring a rerun back to the known name through the page itself.
    await here.goto("/account");
    await nameForm(here).getByLabel("Name").fill(NAME);
    const save = nameForm(here).getByRole("button", { name: "Save name" });
    if (await save.isEnabled()) {
      await save.click();
      await expect(here.getByText("Name saved.")).toBeVisible();
    }
  }
  await signIn(there, ADMIN, FIRST_PASSWORD);
  await expect(menuButton(there, NAME)).toBeVisible();
  expect(hereErrors).toEqual([]);
});

test("the user menu opens the account page", async () => {
  await here.goto("/");
  await menuButton(here, NAME).click();
  await here.getByRole("menuitem", { name: "Account" }).click();
  await expect(here).toHaveURL(/\/account$/);
  await expect(
    here.getByRole("heading", { level: 1, name: "Account" }),
  ).toBeVisible();
  await expect(nameForm(here).getByLabel("Name")).toHaveValue(NAME);
  await expect(nameForm(here)).toContainText(ADMIN);
  await expect(here.locator("body")).not.toContainText(/[–—]| - /);
  await shots(here, "account");
});

test("renaming shows the new name in the header menu at once", async () => {
  const input = nameForm(here).getByLabel("Name");
  await input.fill("   ");
  await nameForm(here).getByRole("button", { name: "Save name" }).click();
  await expect(nameForm(here)).toContainText("Enter your name.");
  await expect(input).toBeFocused();

  await input.fill(`  ${RENAMED}  `);
  await nameForm(here).getByRole("button", { name: "Save name" }).click();
  await expect(here.getByText("Name saved.")).toBeVisible();
  await expect(menuButton(here, RENAMED)).toBeVisible();
  await expect(input).toHaveValue(RENAMED);
  await menuButton(here, RENAMED).click();
  await expect(here.getByRole("menu")).toContainText(RENAMED);
  await shots(here, "account-renamed-menu", false);
  await here.keyboard.press("Escape");

  // The other browser sees it on its next load.
  await there.reload();
  await expect(menuButton(there, RENAMED)).toBeVisible();
  expect(hereErrors).toEqual([]);
});

test("a wrong current password and a mismatch are clear errors that sign nobody out", async () => {
  const form = passwordForm(here);
  await form
    .getByLabel("Current password", { exact: true })
    .fill("not the password at all");
  await form.getByLabel("New password", { exact: true }).fill(NEW_PASSWORD);
  await form
    .getByLabel("Confirm new password", { exact: true })
    .fill(`${NEW_PASSWORD}x`);
  await form.getByRole("button", { name: "Change password" }).click();
  await expect(form).toContainText("The passwords do not match.");
  await expect(
    form.getByLabel("Confirm new password", { exact: true }),
  ).toBeFocused();

  await form
    .getByLabel("Confirm new password", { exact: true })
    .fill(NEW_PASSWORD);
  await form.getByRole("button", { name: "Show new password" }).click();
  await expect(
    form.getByLabel("New password", { exact: true }),
  ).toHaveAttribute("type", "text");
  await form.getByRole("button", { name: "Change password" }).click();
  await expect(form).toContainText("The current password is incorrect.");
  await expect(
    form.getByLabel("Current password", { exact: true }),
  ).toBeFocused();
  await shots(here, "account-wrong-current");
  await form.getByRole("button", { name: "Show new password" }).click();

  await there.reload();
  await expect(menuButton(there, RENAMED)).toBeVisible(); // still signed in
});

test("changing the password signs the other browser out and keeps this one", async () => {
  const form = passwordForm(here);
  await form
    .getByLabel("Current password", { exact: true })
    .fill(FIRST_PASSWORD);
  await form.getByRole("button", { name: "Change password" }).click();
  await expect(here.getByText(CHANGED)).toBeVisible();
  await expect(
    form.getByLabel("Current password", { exact: true }),
  ).toHaveValue("");
  await expect(form.getByLabel("New password", { exact: true })).toHaveValue(
    "",
  );
  await shots(here, "account-password-changed", false);

  // This browser stays signed in, across a reload.
  await here.reload();
  await expect(menuButton(here, RENAMED)).toBeVisible();
  await expect(here).toHaveURL(/\/account$/);

  // The other one lands on the sign in page at its next navigation.
  await there.goto("/");
  await expect(there).toHaveURL(/\/login/);
  await expect(there.getByRole("heading", { name: "Sign in" })).toBeVisible();

  // The old password no longer works, the new one does.
  await signIn(there, ADMIN, FIRST_PASSWORD);
  await expect(there.getByRole("alert")).toHaveText(
    "Incorrect email or password.",
  );
  await shots(there, "old-password-refused");
  await signIn(there, ADMIN, NEW_PASSWORD);
  await expect(menuButton(there, RENAMED)).toBeVisible();
  expect(hereErrors).toEqual([]);
  expect(thereErrors).toEqual([]);
});
