import { expect, test, type BrowserContext, type Page } from "@playwright/test";

import { AgentRun } from "./browser-b12-helpers";
import { PAGES, endLease, windowTitles } from "./browser-b7-helpers";
import { currentLease } from "./browser-helpers";
import {
  caddyUrl,
  clearAttempts,
  cliLink,
  makeShots,
  newContext,
  setPassword,
  watchErrors,
} from "./stack";

/**
 * Browser B12b against the real stack through Caddy, its browser included (compose profile
 * `browser`), no mocks. An assistant's run of person A (in the worker's container, as a run of
 * an agent with browser tools: browser-b12-helpers.ts) holds a lease on B12a's fixture site
 * (tests/browser_pages/b12/login.html, "Example Jobs") behind its login wall, and calls the tool
 * `browser_request_signin`:
 * 1. while A watches on Account: the banner "Sign in to Example Jobs" with the minutes left and
 *    the live view, A at the keyboard (a phone sized screen); B sees no banner; A signs in with
 *    the live view's keyboard and clicks I'm signed in; the run reads "The person signed in" and
 *    goes on on the signed in page, with its lease active again;
 * 2. a second run starts already signed in (the profile kept the login); A clicks Cancel and the
 *    run reads the cancel's sentence, its lease released;
 * 3. nobody watching: the run is answered at once and nothing waits, so its work would wait for
 *    the person in its module (apply A10b's queue).
 * Run it with the group (web/e2e/browser-all.config.ts says how).
 */
test.describe.configure({ mode: "serial", timeout: 180_000 });
test.skip(!caddyUrl, "set E2E_CADDY_URL with the Compose stack running");

const run = Date.now().toString(36);
const A = `e2e-browser-b12a-${run}@example.org`;
const B = `e2e-browser-b12b-${run}@example.org`;
const NAME = `e2e-b12-${run}`;
const SITE = `${PAGES}/b12/login.html?job=42&step=apply`;
const BANNER = { name: "Sign in to Example Jobs" };
const CSRF = { "X-Requested-With": "jobapp" };
const shots = makeShots("browser-b12", "handoff");

let contexts: BrowserContext[] = [];
let pageA: Page;
let pageB: Page;
let errors: string[] = [];

test.beforeAll(async ({ browser }) => {
  for (const [email, name] of [
    [A, "Alex Example"],
    [B, "Blake Example"],
  ] as const) {
    const context = await newContext(browser);
    contexts.push(context);
    const page = await context.newPage();
    errors = [...errors, ...watchErrors(page)];
    await page.goto(cliLink(email));
    await setPassword(page, `browser b12 journey ${run} passphrase`, name);
    if (email === A) pageA = page;
    else pageB = page;
  }
});

test.afterAll(async () => {
  endLease(A);
  for (const page of [pageA, pageB]) {
    await page.request.delete("/api/browser/sessions/current", {
      headers: CSRF,
    });
  }
  for (const context of contexts) await context.close();
  contexts = [];
  clearAttempts([A, B]);
});

/** A run of A on the fixture site, A on Account watching its live view. */
async function watchedRun(): Promise<AgentRun> {
  const agent = new AgentRun(A, SITE);
  const ready = await agent.next();
  expect(ready.step).toBe("ready");
  await pageA.goto("/account");
  await expect(
    pageA.getByText(
      "You are watching your browser. Take over to use it yourself.",
    ),
  ).toBeVisible({ timeout: 45_000 });
  return agent;
}

test("A watches, signs in through the banner's live view, and the run goes on", async () => {
  await pageA.setViewportSize({ width: 390, height: 844 });
  const agent = await watchedRun();
  try {
    agent.ask();
    const banner = pageA.getByRole("region", BANNER);
    await expect(banner).toBeVisible({ timeout: 30_000 });
    await expect(banner).toContainText(/\d+ minutes? left\./);
    await expect(banner).not.toContainText(/—|–| - /);
    await expect(
      banner.getByText(
        "You have the keyboard and the mouse: sign in, then click I'm signed in.",
      ),
    ).toBeVisible({ timeout: 45_000 });
    await expect(
      pageA.getByRole("button", { name: "Take over" }),
    ).not.toBeVisible();
    await shots(pageA, "banner-phone");

    // B, at the same moment, sees no banner of A's.
    await pageB.goto("/account");
    await expect(
      pageB.getByRole("heading", { name: "Your browser" }),
    ).toBeVisible();
    await expect(pageB.getByRole("region", BANNER)).toHaveCount(0);

    // The live view's keyboard: Tab to the name field, the name, Enter.
    await expect
      .poll(windowTitles, { timeout: 30_000 })
      .toContain("Example Jobs: signed out");
    await pageA.getByRole("button", { name: "Keyboard" }).click();
    const keys = pageA.getByLabel("Type into your browser");
    await keys.press("Tab");
    await keys.pressSequentially(NAME);
    await keys.press("Enter");
    await expect
      .poll(windowTitles, { timeout: 30_000 })
      .toContain(`Example Jobs: signed in as ${NAME}`);

    await banner.getByRole("button", { name: "I'm signed in" }).click();
    const answer = await agent.next();
    expect(answer).toMatchObject({ step: "answer", error: false });
    expect(answer.text).toBe(
      `The person signed in; the page is now "Example Jobs: signed in as ${NAME}, job 42, step apply".`,
    );
    expect(answer.signed_in).toBe(true);
    expect(await agent.next()).toMatchObject({
      step: "lease",
      state: "active",
      same: true,
    });
    await expect(pageA.getByRole("region", BANNER)).toHaveCount(0, {
      timeout: 15_000,
    });
    await shots(pageA, "signed-in");
  } finally {
    await agent.close();
  }
});

test("a second run starts signed in; Cancel ends the hand off with its sentence", async () => {
  await pageA.setViewportSize({ width: 1280, height: 800 });
  const agent = new AgentRun(A, SITE);
  try {
    expect(await agent.next()).toMatchObject({
      step: "ready",
      signed_in: true,
    });
    await pageA.goto("/account");
    await expect(
      pageA.getByText(
        "You are watching your browser. Take over to use it yourself.",
      ),
    ).toBeVisible({ timeout: 45_000 });
    agent.ask();
    const banner = pageA.getByRole("region", BANNER);
    await expect(banner).toBeVisible({ timeout: 30_000 });
    await banner.getByRole("button", { name: "Cancel" }).click();

    expect(await agent.next()).toMatchObject({
      step: "answer",
      error: true,
      text: "The person cancelled the sign in. Stop here.",
    });
    expect((await agent.next()).same).toBe(false);
    await expect(pageA.getByRole("region", BANNER)).toHaveCount(0, {
      timeout: 15_000,
    });
    await expect.poll(() => currentLease().lease).toBeNull();
  } finally {
    await agent.close();
  }
});

test("nobody watching: the run is answered at once and nothing waits", async () => {
  await pageA.goto("/");
  const agent = new AgentRun(A, SITE);
  try {
    expect((await agent.next()).step).toBe("ready");
    agent.ask();
    expect(await agent.next()).toMatchObject({
      step: "answer",
      error: true,
      text: "Nobody is watching the browser now. Stop here: the work waits for the person to sign in.",
    });
    expect(await agent.next()).toMatchObject({
      step: "lease",
      state: "active",
      same: true,
    });
  } finally {
    await agent.close();
  }
  await pageA.goto("/account");
  await expect(
    pageA.getByRole("heading", { name: "Your browser" }),
  ).toBeVisible();
  await expect(pageA.getByRole("region", BANNER)).toHaveCount(0);
});

test("no page error was logged", () => {
  expect(errors).toEqual([]);
});
