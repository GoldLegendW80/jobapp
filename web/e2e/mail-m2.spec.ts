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

/**
 * Task M2 of the mail module: the public pages /, /privacy and /terms, against the real stack
 * through Caddy. No Google is involved, so the fake Google is not used; the file is in the mail
 * group because it is named mail-m2.
 *
 *   signed out, open /, follow the link to /privacy, find the Limited Use sentence, follow to
 *   /terms, then to sign in; sign in and check that / leads to /jobs.
 *
 * Then the edge cases: the pages signed out and signed in, no cookie set, no api call and a 200
 * with the api down, 375 px wide, no script from another origin and no font, `noindex`.
 *
 * Locally, on a scratch Compose project (see mail-all.config.ts for the whole group):
 *   E2E_CADDY_URL=http://127.0.0.1:8097 E2E_COMPOSE_PROJECT=jobapp-m2 \
 *     pnpm --dir web exec playwright test e2e/mail-m2.spec.ts
 */
test.describe.configure({ mode: "serial" });
test.skip(!caddyUrl, "set E2E_CADDY_URL with the Compose stack running");

const run = Date.now().toString(36);
const EMAIL = `e2e-m2-${run}@example.org`;
const NAME = "E2E Mail M2";
// Fresh per run, never a common password, never logged.
const PASSWORD = `Pw ${randomBytes(12).toString("base64url")}`;
const shots = makeShots("mail-m2", "mail-m2");

const LIMITED_USE =
  "The use of information received from Google APIs will adhere to the Google API Services User Data Policy, including the Limited Use requirements.";
const PAGES = [
  { path: "/", heading: "jobapp" },
  { path: "/privacy", heading: "Privacy" },
  { path: "/terms", heading: "Terms" },
] as const;

const nav = (page: Page) =>
  page.getByRole("navigation", { name: "Public pages" });

let visitors: BrowserContext;
let members: BrowserContext;

test.beforeAll(async ({ browser }) => {
  // The account of the journey: created through the operator CLI, then forgotten by this browser.
  const setup = await newContext(browser);
  const page = await setup.newPage();
  await page.goto(cliLink(EMAIL));
  await setPassword(page, PASSWORD, NAME);
  await setup.close();
  visitors = await newContext(browser);
  members = await newContext(browser);
});

test.afterAll(async () => {
  await visitors.close();
  await members.close();
  clearAttempts([EMAIL]);
});

test("signed out: / to /privacy, the Limited Use sentence, /terms, sign in, and / leads to /jobs", async () => {
  const page = await visitors.newPage();
  const errors = watchErrors(page);
  await page.goto("/");
  await expect(
    page.getByRole("heading", { level: 1, name: "jobapp" }),
  ).toBeVisible();
  await expect(page).toHaveURL(/\/$/);

  await nav(page).getByRole("link", { name: "Privacy" }).click();
  await expect(page).toHaveURL(/\/privacy$/);
  await expect(page.getByText(LIMITED_USE)).toBeVisible();

  await nav(page).getByRole("link", { name: "Terms" }).click();
  await expect(page).toHaveURL(/\/terms$/);
  await expect(page.getByRole("heading", { name: "Terms" })).toBeVisible();

  await nav(page).getByRole("link", { name: "Sign in" }).click();
  await expect(page).toHaveURL(/\/login$/);
  await signIn(page, EMAIL, PASSWORD);
  await expect(page).toHaveURL(/\/jobs$/);
  await expect(menuButton(page, NAME)).toBeVisible();

  await page.goto("/");
  await expect(page).toHaveURL(/\/jobs$/);
  await expect(page.getByRole("heading", { name: "Jobs" })).toBeVisible();
  expect(errors).toEqual([]);
  await page.close();
});

for (const { path, heading } of PAGES) {
  test(`${path} signed out: 200, no cookie, noindex, nothing from another origin, 375 px`, async ({
    browser,
  }) => {
    const context = await newContext(browser);
    const page = await context.newPage();
    const errors = watchErrors(page);
    const origin = new URL(caddyUrl ?? "").origin;
    const foreign: string[] = [];
    const fonts: string[] = [];
    page.on("request", (request) => {
      if (new URL(request.url()).origin !== origin) foreign.push(request.url());
      if (request.resourceType() === "font") fonts.push(request.url());
    });
    const response = await page.goto(path);
    expect(response?.status()).toBe(200);
    expect(response?.headers()["set-cookie"]).toBeUndefined();
    await expect(
      page.getByRole("heading", { level: 1, name: heading }),
    ).toBeVisible();
    // Neither the page nor the one session check of / leaves a cookie behind.
    expect(await context.cookies()).toEqual([]);
    expect(foreign).toEqual([]);
    expect(fonts).toEqual([]);
    const scripts = await page
      .locator("script[src]")
      .evaluateAll((nodes) => nodes.map((n) => (n as HTMLScriptElement).src));
    for (const src of scripts) expect(new URL(src).origin).toBe(origin);
    await expect(page.locator('meta[name="robots"]')).toHaveAttribute(
      "content",
      "noindex, nofollow",
    );
    // 375 px: makeShots checks for horizontal overflow at that width, light and dark.
    await shots(page, path === "/" ? "home" : path.slice(1));
    expect(errors).toEqual([]);
    await context.close();
  });
}

test("the three pages signed in: 200, no cookie set by them, the same content", async () => {
  const page = await members.newPage();
  await signIn(page, EMAIL, PASSWORD);
  await expect(page).toHaveURL(/\/jobs$/);
  const before = await members.cookies();
  expect(before.length).toBeGreaterThan(0);
  for (const target of ["/privacy", "/terms"]) {
    const response = await page.goto(target);
    expect(response?.status()).toBe(200);
    expect(response?.headers()["set-cookie"]).toBeUndefined();
    await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
    expect(await members.cookies()).toEqual(before);
  }
  await page.goto("/privacy");
  await expect(page.getByText(LIMITED_USE)).toBeVisible();
  await page.goto("/");
  await expect(page).toHaveURL(/\/jobs$/);
  await page.close();
});

test("with the api down the three pages still answer 200 and say what they say", async ({
  browser,
}) => {
  const context = await newContext(browser);
  const page = await context.newPage();
  const apiCalls: string[] = [];
  await page.route("**/api/**", (route) => {
    apiCalls.push(new URL(route.request().url()).pathname);
    return route.fulfill({
      status: 502,
      contentType: "application/json",
      body: JSON.stringify({ detail: "down" }),
    });
  });
  for (const target of ["/privacy", "/terms"]) {
    const response = await page.goto(target);
    expect(response?.status()).toBe(200);
    await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
  }
  await expect(page.getByRole("heading", { name: "Terms" })).toBeVisible();
  // The static pages do not touch the api at all.
  expect(apiCalls).toEqual([]);
  // The home page asks once who is signed in, and shows itself when the answer is an error.
  const response = await page.goto("/");
  expect(response?.status()).toBe(200);
  await expect(
    page.getByRole("heading", { level: 1, name: "jobapp" }),
  ).toBeVisible();
  expect(apiCalls).toEqual(["/api/auth/me"]);
  await context.close();
});
