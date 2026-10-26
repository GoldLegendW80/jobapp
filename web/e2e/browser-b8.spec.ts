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
  setPassword,
  watchErrors,
} from "./stack";

/**
 * Browser B8 against the real stack through Caddy, its browser and the static pages of
 * tests/browser_pages/b8/ (tests/browser-pages.compose.yml points BROWSER_SOURCES_BASE_URL of the
 * api and the worker at them), no mocks: never a real Google or Business France page. Person A,
 * on a phone sized page, adds three job sites in the Search tab (Google Careers "three", Google
 * Careers "changed", a changed layout, and Business France VIE "one"), runs Find jobs, and reads
 * in the summary what each site found in plain words; the postings are on the jobs page, and a
 * posting's page names its source. Person B's jobs page holds none of them.
 *
 * The sweep reads one page every 5 to 15 s (app/browser/sources, PAGE_PAUSE_S): 4 pages wait,
 * so the sweep takes about a minute.
 *
 * Run it with the group (web/e2e/browser-all.config.ts says how).
 */
test.describe.configure({ mode: "serial", timeout: 300_000 });
test.skip(!caddyUrl, "set E2E_CADDY_URL with the Compose stack running");

const run = Date.now().toString(36);
const A = `e2e-browser-b8a-${run}@example.org`;
const B = `e2e-browser-b8b-${run}@example.org`;
const CSRF = { "X-Requested-With": "jobapp" };
const PHONE = { width: 375, height: 812 };
const shots = makeShots("browser-b8");
// The postings of the fixture pages b8/google/jobs/results/three.html and b8/vie/.../one.html.
const GOOGLE = [
  "AI Engineer, Agents",
  "Applied AI Engineer",
  "Forward Deployed AI Engineer",
];
const VIE = "VIE AI Engineer";

const SKILLS = {
  years_experience: 3.5,
  have: [
    { name: "agents", terms: ["agent", "agents"] },
    { name: "python", terms: ["python"] },
  ],
  adjacent: [],
  none: [],
  gates: [],
};

const terms = (list: string[]) => ({ terms: list, advanced_regex: false });

let contexts: BrowserContext[] = [];
let pageA: Page;
let pageB: Page;
const errors: string[][] = [];

async function saveSection(
  request: APIRequestContext,
  section: string,
  change: (current: Record<string, unknown>) => unknown,
) {
  const profile = await request.get("/api/profile");
  expect(profile.ok()).toBe(true);
  const body = (await profile.json()) as {
    revision: number;
    sections: Record<string, Record<string, unknown> | null>;
  };
  const saved = await request.put(`/api/profile/${section}`, {
    headers: CSRF,
    data: {
      base_revision: body.revision,
      data: change(body.sections[section] ?? {}),
    },
  });
  expect(saved.ok(), await saved.text()).toBe(true);
}

async function titlesOf(page: Page): Promise<string[]> {
  const answer = await page.request.get("/api/jobs?limit=200");
  expect(answer.ok()).toBe(true);
  const body = (await answer.json()) as { items: { title: string }[] };
  return body.items.map((job) => job.title);
}

test.beforeAll(async ({ browser }) => {
  for (const [email, name] of [
    [A, "Alex Example"],
    [B, "Blake Example"],
  ] as const) {
    const context = await newContext(browser);
    contexts.push(context);
    const page = await context.newPage();
    errors.push(watchErrors(page));
    await page.goto(cliLink(email));
    await setPassword(page, `browser b8 journey ${run} passphrase`, name);
    await saveSection(page.request, "skills", () => SKILLS);
    // What the search keeps: the fixture titles, in the fixture places, of any age.
    await saveSection(page.request, "search", (search) => ({
      ...search,
      title_include: terms(["ai engineer"]),
      title_exclude: terms([]),
      location: terms(["paris", "london", "milan"]),
      days: 365,
      boards: [],
    }));
    if (email === A) pageA = page;
    else pageB = page;
  }
});

test.afterAll(async () => {
  for (const context of contexts) await context.close();
  contexts = [];
  clearAttempts([A, B]);
});

test("A adds three job sites in the Search tab, on a phone", async () => {
  await pageA.setViewportSize(PHONE);
  await pageA.goto("/profile?tab=search");
  const form = pageA.getByRole("form", { name: "Search" });
  await expect(form.getByText("No site yet.")).toBeVisible();
  const add = form.getByRole("button", { name: "Add site" });
  for (let n = 0; n < 3; n += 1) await add.click();
  const sites = form.getByRole("list", { name: "Job sites in your browser" });
  await expect(sites.getByRole("listitem")).toHaveCount(3);
  // Company sites and LinkedIn come later: listed, not to be picked.
  await expect(
    sites
      .getByLabel("Site 1", { exact: true })
      .locator("option", { hasText: "LinkedIn (coming later)" }),
  ).toBeDisabled();
  await form.getByLabel("Search words of site 1").fill("three");
  await form.getByLabel("Search words of site 2").fill("changed");
  await sites.getByLabel("Site 3", { exact: true }).selectOption("vie");
  await form.getByLabel("Search words of site 3").fill("one");
  // Nothing wider than the phone.
  const wide = await pageA.evaluate(
    () => document.documentElement.scrollWidth - window.innerWidth,
  );
  expect(wide).toBeLessThanOrEqual(0);
  await shots(pageA, "search-tab-job-sites");
  await form.getByRole("button", { name: "Save search" }).click();
  await expect(pageA.getByText("Search saved.")).toBeVisible();

  const profile = await pageA.request.get("/api/profile");
  const body = (await profile.json()) as {
    sections: { search: { browser_sources: unknown[] } };
  };
  expect(body.sections.search.browser_sources).toEqual([
    // Browser B10: every entry carries LinkedIn's acceptance fields, null for other sites.
    ...[
      { kind: "google_careers", query: "three" },
      { kind: "google_careers", query: "changed" },
      { kind: "vie", query: "one" },
    ].map((site) => ({
      ...site,
      location: null,
      enabled: true,
      accepted_at: null,
      text_version: null,
    })),
  ]);
});

test("A finds jobs: the sites are read in the browser, and the postings listed", async () => {
  await pageA.setViewportSize({ width: 1280, height: 800 });
  await pageA.goto("/jobs");
  await expect(pageA.getByRole("main").getByRole("status").first()).toHaveText(
    "0 postings",
  );
  await pageA.getByRole("button", { name: "Find jobs" }).click();
  const setup = pageA.getByRole("dialog", { name: "Find jobs setup" });
  await expect(setup).toBeVisible();
  await setup.getByRole("button", { name: "Save and Start" }).click();
  const progress = pageA.getByRole("region", { name: "Sweep in progress" });
  await expect(progress).toContainText("Reading job sites in your browser", {
    timeout: 60_000,
  });
  const dialog = pageA.getByRole("dialog", { name: "4 new postings found" });
  await expect(dialog).toBeVisible({ timeout: 240_000 });
  const sites = dialog.getByRole("region", {
    name: "Job sites in your browser",
  });
  await expect(sites.getByRole("listitem")).toHaveText([
    "Google Careers, three: 3 postings found, 3 new.",
    "Google Careers, changed: 0 postings found, 0 new. The page looked different from what the app knows, so nothing was kept from it.",
    "Business France VIE, one: 1 posting found, 1 new.",
  ]);
  await shots(pageA, "find-jobs-job-sites", false);
  await dialog.getByRole("button", { name: "Done" }).click();

  const table = pageA.getByRole("table", { name: "Postings" });
  for (const title of [...GOOGLE, VIE])
    await expect(table).toContainText(title);
  await expect(pageA.getByRole("main").getByRole("status").first()).toHaveText(
    "4 postings",
  );
  await shots(pageA, "jobs-from-job-sites");

  // A posting's page names the site and the words it was found with.
  await table.getByRole("link", { name: GOOGLE[1] }).click();
  await expect(pageA.getByRole("main")).toContainText("Google Careers, three");
  await pageA.goBack();
  await table.getByRole("link", { name: VIE }).click();
  await expect(pageA.getByRole("main")).toContainText(
    "Business France VIE, one",
  );
});

test("B sees none of A's postings", async () => {
  await pageB.goto("/jobs");
  await expect(pageB.getByRole("main").getByRole("status").first()).toHaveText(
    "0 postings",
  );
  expect(await titlesOf(pageB)).toEqual([]);
  expect((await titlesOf(pageA)).sort()).toEqual([...GOOGLE, VIE].sort());
});

test("no page error on either side", () => {
  for (const seen of errors) expect(seen).toEqual([]);
});
