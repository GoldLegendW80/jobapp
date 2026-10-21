import { expect, test, type BrowserContext, type Page } from "@playwright/test";

import { PAGES } from "./browser-b7-helpers";
import {
  caddyUrl,
  cliLink,
  newContext,
  setPassword,
  watchErrors,
} from "./stack";

/**
 * Browser B9: company career sites read by the finder agent. Person A adds a careers page
 * source pointed at the fixture site (tests/browser_pages/b9/careers.html), runs a sweep on the
 * fake model backend (the recorded stream app/agents/fake_streams/browser_finder.json reads the
 * page, follows its next page and reports two postings), and sees the postings on the jobs page.
 * The page also asks the assistant to open another site and shows a Load more button: the
 * finder has no click and code refuses the other host, so only the site's postings arrive.
 */
test.describe.configure({ mode: "serial", timeout: 240_000 });
test.skip(!caddyUrl, "set E2E_CADDY_URL with the Compose stack running");

const run = Date.now().toString(36);
const A = `e2e-browser-b9a-${run}@example.org`;
const SITE = `${PAGES}/b9/careers.html`;
const CSRF = { "X-Requested-With": "jobapp" };
const tail = () => Math.random().toString(36).slice(2, 12).padEnd(10, "x");
const KEY = `sk-ant-api03-e2efinder${tail()}${tail()}Fn9b`;

const terms = (list: string[]) => ({ terms: list, advanced_regex: false });

let context: BrowserContext;
let page: Page;
let errors: string[] = [];

type Sweep = {
  status: string;
  new: number;
  error: string | null;
};

test.beforeAll(async ({ browser }) => {
  context = await newContext(browser);
  page = await context.newPage();
  errors = watchErrors(page, []);
  await page.goto(cliLink(A));
  await setPassword(
    page,
    `browser b9 journey ${run} passphrase`,
    "Alex Example",
  );
});

test.afterAll(async () => {
  await context.close();
});

test("a careers page source is read by the finder and its postings are listed", async () => {
  const saved = await page.request.put("/api/me/model-credential", {
    headers: CSRF,
    data: { kind: "anthropic_api_key", secret: KEY },
  });
  expect(saved.status()).toBe(200);

  const profile = await page.request.get("/api/profile");
  const { revision } = (await profile.json()) as { revision: number };
  const search = await page.request.put("/api/profile/search", {
    headers: CSRF,
    data: {
      base_revision: revision,
      data: {
        title_include: terms(["engineer"]),
        title_exclude: terms([]),
        location: terms(["paris", "lyon", "france"]),
        contract_types: [],
        remote_stance: "any",
        min_fit: 70,
        days: 365,
        boards: [],
        never_apply: [],
        browser_sources: [{ kind: "site", query: SITE, enabled: true }],
      },
    },
  });
  expect(search.ok(), await search.text()).toBe(true);

  const started = await page.request.post("/api/jobs/sweeps", {
    headers: CSRF,
  });
  expect(started.ok(), await started.text()).toBe(true);
  let last: Sweep | undefined;
  await expect
    .poll(
      async () => {
        const answer = await page.request.get("/api/jobs/sweeps?limit=1");
        last = ((await answer.json()) as { items: Sweep[] }).items[0];
        return last?.status;
      },
      { timeout: 200_000, intervals: [2_000] },
    )
    .toBe("done");
  expect(last?.new, JSON.stringify(last)).toBe(2);

  await page.goto("/jobs");
  const main = page.getByRole("main");
  await expect(main).toContainText("AI Engineer");
  await expect(main).toContainText("ML Engineer");
  await expect(main).not.toContainText("Robot Technician"); // behind Load more: never clicked
  await expect(page.locator("body")).not.toContainText(/[–—]| - /);
  expect(errors).toEqual([]);
});
