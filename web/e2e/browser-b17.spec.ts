import { execFileSync } from "node:child_process";

import { expect, test, type BrowserContext, type Page } from "@playwright/test";

import { windowTitles } from "./browser-b7-helpers";
import { currentLease } from "./browser-helpers";
import {
  caddyUrl,
  cliLink,
  makeShots,
  newContext,
  root,
  setPassword,
  watchErrors,
} from "./stack";

/**
 * Browser B17: sign in to a job website once, then Find jobs reads it signed in. Against the
 * real stack through Caddy, its browser, the fake model backend and the static pages of
 * tests/browser_pages/ (tests/browser-pages.compose.yml), no mocks: never a real job site.
 *
 * The website is J15's invented one under the name members.example-robotics.test, for members
 * only (server.py): every page answers only to a member's cookie, else its sign in page.
 * - Person A adds it in Find jobs and clicks its Sign in: the live view opens on its sign in page
 *   in her own browser, she has the keyboard, there is no debugging port; she types a name with
 *   the live view's keyboard (Tab, the name, Enter) and clicks Done: the row says Signed in.
 * - Save and Start: the interactive finder (app/agents/fake_streams/browser_discovery.json, J15's
 *   recorded model) searches the website in A's own browser, signed in: every page it opened came
 *   with her member's cookie, the site saw GET requests only, and the posting's Send application
 *   button and the address the posting asks for were refused, as on the anonymous website.
 * - The site ends every login (GET /b17/expire): the next search meets the sign in page, ends the
 *   website `platform_signin`, the summary asks to sign in again and the row says Sign in again.
 * - Person B has no login of the website, and B's Done reads B's own browser only.
 *
 * Run it with the group (web/e2e/browser-all.config.ts says how).
 */
test.describe.configure({ mode: "serial", timeout: 360_000 });
test.skip(!caddyUrl, "set E2E_CADDY_URL with the Compose stack running");

const run = Date.now().toString(36);
const A = `e2e-browser-b17a-${run}@example.org`;
const B = `e2e-browser-b17b-${run}@example.org`;
const NAME = `e2e-alex-${run}`;
const HOST = "members.example-robotics.test";
const SITE = `http://${HOST}:8080/j15/`;
const CSRF = { "X-Requested-With": "jobapp" };
const SIGN_IN_AGAIN = "Use Sign in beside it in Find jobs, then search again.";
const shots = makeShots("browser-b17");
const tail = () => Math.random().toString(36).slice(2, 12).padEnd(10, "x");
const KEY = `sk-ant-api03-e2emembers${tail()}${tail()}Fb17`;
const terms = (list: string[]) => ({ terms: list, advanced_regex: false });

type Source = {
  kind: string;
  host?: string | null;
  mode?: string | null;
  found: number;
  error: string | null;
};
type Sweep = { id: string; status: string; sources: Source[] };
type Seen = { method: string; path: string; at: number; member?: boolean };
type Site = { host: string; state: string };

function compose(...args: string[]): string {
  const project = process.env.E2E_COMPOSE_PROJECT;
  if (!project)
    throw new Error("E2E_COMPOSE_PROJECT names the journey's own stack");
  return execFileSync(
    "docker",
    [
      "compose",
      "-p",
      project,
      "-f",
      "compose.yml",
      "--profile",
      "browser",
      ...args,
    ],
    { cwd: root, encoding: "utf8", timeout: 60_000 },
  );
}

/** A path of the pages server, asked inside its container. */
function pages(path: string): string {
  const read = `import urllib.request;print(urllib.request.urlopen('http://127.0.0.1:8080${path}').read().decode())`;
  return compose("exec", "-T", "pages", "python", "-c", read);
}

const siteLog = () => JSON.parse(pages("/b17/log")) as Seen[];

async function sites(page: Page): Promise<Site[]> {
  const answer = await page.request.get("/api/browser/sites");
  expect(answer.status()).toBe(200);
  return ((await answer.json()) as { sites: Site[] }).sites;
}

async function lastSweep(page: Page, after: string | null): Promise<Sweep> {
  let last: Sweep | undefined;
  await expect
    .poll(
      async () => {
        const answer = await page.request.get("/api/jobs/sweeps?limit=1");
        last = ((await answer.json()) as { items: Sweep[] }).items[0];
        return last && last.id !== after ? last.status : "waiting";
      },
      { timeout: 300_000, intervals: [2_000] },
    )
    .toBe("done");
  if (!last) throw new Error("no sweep");
  return last;
}

async function openSetup(page: Page) {
  await page.goto("/jobs");
  await page.getByRole("button", { name: "Find jobs" }).click();
  const setup = page.getByRole("dialog", { name: "Find jobs setup" });
  await expect(setup).toBeVisible();
  return setup;
}

async function saveAndStart(page: Page, after: string | null) {
  const setup = page.getByRole("dialog", { name: "Find jobs setup" });
  await setup.getByRole("button", { name: "Save and Start" }).click();
  await expect(setup).toBeHidden({ timeout: 30_000 });
  const sweep = await lastSweep(page, after);
  const summary = page.getByRole("dialog", { name: /new posting/ });
  await expect(summary).toBeVisible({ timeout: 60_000 });
  const line = summary
    .getByRole("region", { name: "Job sites in your browser" })
    .getByRole("listitem")
    .filter({ hasText: `${HOST}:` });
  return { sweep, summary, line };
}

let context: BrowserContext;
let page: Page;
let errors: string[] = [];
let sweepId: string | null = null;
let before = 0;

test.beforeAll(async ({ browser }) => {
  context = await newContext(browser);
  page = await context.newPage();
  errors = watchErrors(page, []);
  await page.goto(cliLink(A));
  await setPassword(page, `browser b17 journey ${run} passphrase`, "Alex Example");
  const saved = await page.request.put("/api/me/model-credential", {
    headers: CSRF,
    data: { kind: "anthropic_api_key", secret: KEY },
  });
  expect(saved.status()).toBe(200);
  const profile = (await (await page.request.get("/api/profile")).json()) as {
    revision: number;
  };
  const search = await page.request.put("/api/profile/search", {
    headers: CSRF,
    data: {
      base_revision: profile.revision,
      data: {
        title_include: terms(["engineer"]),
        title_exclude: terms([]),
        location: terms(["Paris"]),
        contract_types: [],
        remote_stance: "any",
        min_fit: 70,
        days: 365,
        boards: [],
        never_apply: [],
        browser_sources: [],
      },
    },
  });
  expect(search.ok(), await search.text()).toBe(true);
  before = siteLog().length;
});

test.afterAll(async () => {
  await context.close();
});

test("A signs in to the members website from its row in Find jobs", async () => {
  const setup = await openSetup(page);
  await setup.getByLabel("Custom website URL").fill(SITE);
  await setup.getByRole("button", { name: "Add website" }).click();
  const row = setup.getByRole("listitem").filter({ hasText: SITE });
  await expect(row).toBeVisible();
  expect(await sites(page)).toEqual([]);
  await expect(row).not.toContainText("Signed in");

  await row.getByRole("button", { name: `Sign in to ${HOST}` }).click();
  await expect(
    setup.getByText(
      "This is your own session: you have the keyboard and the mouse.",
    ),
  ).toBeVisible({ timeout: 45_000 });
  expect(currentLease().lease?.purpose).toBe("signin");
  await expect
    .poll(windowTitles, { timeout: 30_000 })
    .toContain("Example Robotics members: sign in");

  // The live view's keyboard: Tab to the name field, the name, Enter. The app types nothing.
  await setup.getByRole("button", { name: "Keyboard" }).click();
  const keys = setup.getByLabel("Type into your browser");
  await keys.press("Tab");
  await keys.pressSequentially(NAME);
  await keys.press("Enter");
  await expect
    .poll(windowTitles, { timeout: 30_000 })
    .toContain("Example Robotics jobs");

  await row.getByRole("button", { name: "Done" }).click();
  await expect(row).toContainText("Signed in", { timeout: 45_000 });
  await expect(
    row.getByRole("button", { name: `Sign in to ${HOST}` }),
  ).toHaveText("Sign in");
  expect(currentLease().lease).toBeNull();
  expect(await sites(page)).toMatchObject([{ host: HOST, state: "signed_in" }]);
  await shots(page, "signed-in");
});

test("Find jobs reads the members only postings signed in, and submits nothing", async () => {
  const { sweep, summary } = await saveAndStart(page, null);
  sweepId = sweep.id;
  await expect(summary).not.toContainText(SIGN_IN_AGAIN);
  await expect(summary).not.toContainText(/[–—]| - /);
  await page.keyboard.press("Escape");
  await expect(summary).toBeHidden();

  const own = sweep.sources.find((item) => item.kind === "custom");
  const detail = JSON.stringify(sweep.sources);
  expect(own, detail).toMatchObject({ mode: "llm_browser", host: HOST });
  expect(own?.error, detail).not.toBe("platform_signin");
  expect(own?.found ?? 0, detail).toBeGreaterThanOrEqual(1);

  // What the website saw: its members only pages, each with A's member's cookie, GET only;
  // the refused Send application button sent nothing, the asked address was never opened.
  const seen = siteLog().slice(before);
  const read = seen.filter((item) => item.path.startsWith("/j15/"));
  expect(read.map((item) => item.path)).toEqual(
    expect.arrayContaining([
      "/j15/search?q=engineer&location=Paris&page=2",
      "/j15/jobs/robot-engineer.html",
    ]),
  );
  expect(read.every((item) => item.member === true), JSON.stringify(seen)).toBe(
    true,
  );
  expect(seen.every((item) => item.method === "GET")).toBe(true);
  expect(seen.some((item) => item.path.includes("send"))).toBe(false);
  expect(await sites(page)).toMatchObject([{ host: HOST, state: "signed_in" }]);
  before = siteLog().length;
});

test("an ended login stops the search and asks A to sign in again", async () => {
  pages("/b17/expire");
  await openSetup(page);
  const { sweep, line, summary } = await saveAndStart(page, sweepId);
  await expect(line).toContainText(SIGN_IN_AGAIN);
  await shots(page, "sign-in-again-summary");
  await page.keyboard.press("Escape");
  await expect(summary).toBeHidden();

  const own = sweep.sources.find((item) => item.kind === "custom");
  expect(own, JSON.stringify(sweep.sources)).toMatchObject({
    host: HOST,
    error: "platform_signin",
    found: 0,
  });
  expect(await sites(page)).toMatchObject([{ host: HOST, state: "expired" }]);
  // The search stopped at the sign in page: nothing read as a member, nothing sent.
  const seen = siteLog().slice(before);
  expect(seen.some((item) => item.member === true)).toBe(false);
  expect(seen.every((item) => item.method === "GET")).toBe(true);

  const setup = await openSetup(page);
  const row = setup.getByRole("listitem").filter({ hasText: SITE });
  await expect(
    row.getByRole("button", { name: `Sign in to ${HOST}` }),
  ).toHaveText("Sign in again");
  await expect(row).toContainText("Your login on this website has ended.");
  await shots(page, "sign-in-again");
  await page.keyboard.press("Escape");
  expect(errors).toEqual([]);
});

test("B has no login of the website, and B's Done reads only B's browser", async ({
  browser,
}) => {
  const other = await newContext(browser);
  const pageB = await other.newPage();
  await pageB.goto(cliLink(B));
  await setPassword(pageB, `browser b17 journey ${run} b passphrase`, "Bo Example");
  expect(await sites(pageB)).toEqual([]);
  const done = await pageB.request.post("/api/browser/sites/done", {
    headers: CSRF,
    data: { address: SITE },
  });
  expect(done.status()).toBe(200);
  expect(await done.json()).toMatchObject({ host: HOST, state: "not_signed_in" });
  const mine = (await (await pageB.request.get("/api/browser")).json()) as {
    profile: { exists: boolean };
  };
  expect(mine.profile.exists).toBe(false);
  expect(await sites(pageB)).toEqual([]);
  expect(await sites(page)).toMatchObject([{ host: HOST, state: "expired" }]);
  await other.close();
});
