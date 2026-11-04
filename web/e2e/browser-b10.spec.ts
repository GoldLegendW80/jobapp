import { execFileSync } from "node:child_process";

import {
  expect,
  test,
  type APIRequestContext,
  type BrowserContext,
  type Page,
} from "@playwright/test";

import {
  caddyUrl,
  clearAttempts,
  cliLink,
  makeShots,
  newContext,
  root,
  setPassword,
  watchErrors,
} from "./stack";

/**
 * Browser B10's guards on LinkedIn searched as a job website (owner, 2026-10-28: every search
 * runs the job websites ticked in Find jobs alone, the LinkedIn reader no longer runs), against
 * the real stack through Caddy, its browser, the fake model backend and the static pages of
 * tests/browser_pages/ (tests/browser-pages.compose.yml), no mocks: never a real LinkedIn page.
 * Under BROWSER_SOURCES_BASE_URL LinkedIn's search opens the pages service's b8/linkedin/; the
 * journey makes its next opening LinkedIn's security check or its sign in page
 * (tests/browser_pages/server.py, GET /b10/next, asked from inside the container).
 *
 * - Person C ticks LinkedIn in Find jobs and her search meets the security check on its first
 *   page. Since B20 (owner, 2026-11-04) the browser search no longer pauses all LinkedIn work for
 *   a week: the person solves a check (the agent asks, `browser_ask_person`), so no pause is
 *   recorded, the LinkedIn card shows none, and a second search that day searches LinkedIn again.
 * - Person D's search meets the sign in page: no wall ends the website by itself (B20), so the
 *   summary does not say LinkedIn needs her to sign in; she holds no lease and waits in no queue,
 *   and nothing is saved.
 * - Person B ticked nothing: LinkedIn stays off for B, with no pause and no search.
 *
 * Run it with the group (web/e2e/browser-all.config.ts says how).
 */
test.describe.configure({ mode: "serial", timeout: 360_000 });
test.skip(!caddyUrl, "set E2E_CADDY_URL with the Compose stack running");

const run = Date.now().toString(36);
const B = `e2e-browser-b10b-${run}@example.org`;
const C = `e2e-browser-b10c-${run}@example.org`;
const D = `e2e-browser-b10d-${run}@example.org`;
const CSRF = { "X-Requested-With": "jobapp" };
const shots = makeShots("browser-b10");
const tail = () => Math.random().toString(36).slice(2, 12).padEnd(10, "x");
const KEY = `sk-ant-api03-e2ediscovery${tail()}${tail()}Fb10`;
const terms = (list: string[]) => ({ terms: list, advanced_regex: false });
const PAUSED = /paused for a week|paused until|needs you to sign in/;

type Job = { id: string; title: string; url: string | null };
type LinkedIn = {
  enabled: boolean;
  accepted: boolean;
  paused_until: string | null;
  needs_signin: boolean;
  last_sweep_at: string | null;
};
type Source = { kind: string; error: string | null; found: number };

let contexts: BrowserContext[] = [];
const pages: Record<string, Page> = {};
const errors: string[][] = [];

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

/** The wall LinkedIn's start page shows next, once (server.py, GET /b10/next). */
function nextWall(wall: "checkpoint" | "signin" | ""): void {
  const ask = `import urllib.request;print(urllib.request.urlopen('http://127.0.0.1:8080/b10/next?wall=${wall}').read().decode())`;
  const answer = JSON.parse(
    compose("exec", "-T", "pages", "python", "-c", ask),
  ) as { wall: string | null };
  expect(answer.wall).toBe(wall || null);
}

async function jobsOf(page: Page): Promise<Job[]> {
  const answer = await page.request.get("/api/jobs?limit=200");
  expect(answer.ok()).toBe(true);
  return ((await answer.json()) as { items: Job[] }).items;
}

async function linkedinOf(page: Page): Promise<LinkedIn> {
  const answer = await page.request.get("/api/browser/linkedin");
  expect(answer.ok()).toBe(true);
  return (await answer.json()) as LinkedIn;
}

async function lastSources(request: APIRequestContext): Promise<Source[]> {
  const answer = await request.get("/api/jobs/sweeps?limit=1");
  const body = (await answer.json()) as {
    items: { status: string; sources: Source[] }[];
  };
  expect(body.items[0]?.status).toBe("done");
  return body.items[0]?.sources ?? [];
}

/**
 * Find jobs with LinkedIn ticked (the tick and the save are the permission, jobs J17), then the
 * summary's dialog once the search ended.
 */
async function findJobs(page: Page) {
  await page.goto("/jobs");
  await page.getByRole("button", { name: "Find jobs" }).click();
  const setup = page.getByRole("dialog", { name: "Find jobs setup" });
  await expect(setup).toBeVisible();
  await setup.getByRole("checkbox", { name: "LinkedIn", exact: true }).check();
  // These guards are the browser search's: LinkedIn's public job search is the default
  // (owner, 2026-10-30), so the journey chooses the browser.
  await setup
    .getByRole("radio", { name: "Search with the browser, signed in" })
    .check();
  await setup.getByRole("button", { name: "Save and Start" }).click();
  const dialog = page.getByRole("dialog", {
    name: /new postings? found|No new posting|The sweep stopped/,
  });
  await expect(dialog).toBeVisible({ timeout: 300_000 });
  return dialog;
}

const linkedinLine = (dialog: ReturnType<Page["getByRole"]>) =>
  dialog
    .getByRole("region", { name: "Job sites in your browser" })
    .getByRole("listitem")
    .filter({ hasText: "LinkedIn:" });

test.beforeAll(async ({ browser }) => {
  for (const [email, name] of [
    [B, "Blake Example"],
    [C, "Casey Example"],
    [D, "Dana Example"],
  ] as const) {
    const context = await newContext(browser);
    contexts.push(context);
    const page = await context.newPage();
    errors.push(watchErrors(page));
    await page.goto(cliLink(email));
    await setPassword(page, `browser b10 journey ${run} passphrase`, name);
    // Model access for the website's finder, and the words a website search types.
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
          location: terms([]),
          contract_types: [],
          remote_stance: "any",
          min_fit: 70,
          days: 365,
          never_apply: [],
        },
      },
    });
    expect(search.ok(), await search.text()).toBe(true);
    pages[email] = page;
  }
});

test.afterAll(async () => {
  nextWall("");
  for (const context of contexts) await context.close();
  contexts = [];
  clearAttempts([B, C, D]);
});

test("C meets LinkedIn's security check: no pause is recorded, and LinkedIn is searched again", async () => {
  const page = pages[C]!;
  nextWall("checkpoint");
  const dialog = await findJobs(page);
  await expect(linkedinLine(dialog)).not.toContainText(PAUSED);
  await shots(page, "find-jobs-checkpoint", false);
  await dialog.getByRole("button", { name: "Done" }).click();
  const first = (await lastSources(page.request)).find(
    (source) => source.kind === "linkedin",
  );
  expect(first?.found).toBe(0);
  expect(first?.error ?? "").not.toMatch(/paused|checkpoint/);

  // No 7 day pause: nothing recorded, nothing on the LinkedIn card.
  const state = await linkedinOf(page);
  expect(state.paused_until).toBeNull();
  await page.goto("/account");
  const card = page.getByRole("region", { name: "LinkedIn", exact: true });
  await expect(card).toBeVisible();
  await expect(card).not.toContainText(/security check|paused/);
  await shots(page, "linkedin-not-paused");

  // A second search the same day opens LinkedIn again.
  const again = await findJobs(page);
  await expect(linkedinLine(again)).not.toContainText(PAUSED);
  await again.getByRole("button", { name: "Done" }).click();
  const second = (await lastSources(page.request)).find(
    (source) => source.kind === "linkedin",
  );
  expect(second?.error ?? "").not.toMatch(/paused|checkpoint/);
});

test("D meets LinkedIn's sign in page: no wall ends the website, no hand off", async () => {
  const page = pages[D]!;
  nextWall("signin");
  const dialog = await findJobs(page);
  await expect(linkedinLine(dialog)).not.toContainText(PAUSED);
  await dialog.getByRole("button", { name: "Done" }).click();
  const source = (await lastSources(page.request)).find(
    (item) => item.kind === "linkedin",
  );
  expect(source?.error ?? "").not.toMatch(/signin/);
  expect(source?.found).toBe(0);
  const state = await linkedinOf(page);
  expect(state.paused_until).toBeNull();
  expect(state.needs_signin).toBe(false);
  await shots(page, "linkedin-sign-in-page");
  // No hand off: D holds no lease of the browser and waits in no queue.
  const browserState = await page.request.get("/api/browser");
  expect(browserState.ok()).toBe(true);
  const held = (await browserState.json()) as {
    lease: unknown;
    queue: unknown[];
  };
  expect([held.lease, held.queue]).toEqual([null, []]);
  expect(await jobsOf(page)).toEqual([]);
});

test("B ticked nothing: LinkedIn stays off for B, and B reads nothing of the others", async () => {
  const page = pages[B]!;
  const state = await linkedinOf(page);
  expect([
    state.enabled,
    state.accepted,
    state.paused_until,
    state.needs_signin,
    state.last_sweep_at,
  ]).toEqual([false, false, null, false, null]);
  expect(await jobsOf(page)).toEqual([]);
});

test("no page error on any side", () => {
  for (const seen of errors) expect(seen).toEqual([]);
});
