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
 * Browser B10 against the real stack through Caddy, its browser and the static pages of
 * tests/browser_pages/b10/linkedin/ (reached as b8/linkedin/ under BROWSER_SOURCES_BASE_URL of
 * tests/browser-pages.compose.yml), no mocks: never a real LinkedIn page. The Greenhouse board
 * `fake-b10` is the fake ATS of tests/ats-stub.compose.yml, which the stack of the group runs.
 *
 * - Person A, on a phone sized page, turns LinkedIn on in "Your browser": the sentence word for
 *   word, then "I accept, turn LinkedIn on". Find jobs reads the search "agents" and its
 *   postings: one keeps the company's Apply address as its address, and the one Greenhouse
 *   also lists is one row naming both sources.
 * - Person C's search meets LinkedIn's security check on a posting page: the card shows the
 *   pause and its date, a second sweep that day does not search LinkedIn, and an application on
 *   the LinkedIn row found before the check is blocked before any browser work.
 * - Person D's search meets the sign in page: the card says "LinkedIn needs you to sign in"
 *   and no hand off starts (no lease of D's browser).
 * - Person B's switch stays off, and B reads none of the others' state.
 *
 * A LinkedIn page waits 10 to 30 s (app/browser/sources/linkedin.py, PAUSE_S): A's sweep reads
 * up to four pages, so it takes up to two minutes.
 *
 * Run it with the group (web/e2e/browser-all.config.ts says how).
 */
test.describe.configure({ mode: "serial", timeout: 360_000 });
test.skip(!caddyUrl, "set E2E_CADDY_URL with the Compose stack running");

const run = Date.now().toString(36);
const A = `e2e-browser-b10a-${run}@example.org`;
const B = `e2e-browser-b10b-${run}@example.org`;
const C = `e2e-browser-b10c-${run}@example.org`;
const D = `e2e-browser-b10d-${run}@example.org`;
const CSRF = { "X-Requested-With": "jobapp" };
const PHONE = { width: 375, height: 812 };
const shots = makeShots("browser-b10");
const SENTENCE =
  "LinkedIn's terms forbid automated use of its site, and your account may be limited or closed; the app keeps to the pace of a person, but the risk is yours.";
// The postings of b10/linkedin/jobs/search/agents.html, and the one of checkpoint.html read
// before the security check.
const AGENTS = "AI Engineer, Agents";
const APPLIED = "Applied AI Engineer";
const FORWARD = "Forward Deployed AI Engineer";
const BEFORE_CHECK = "AI Engineer, Evaluation";
const COMPANY_URL = "https://careers.example.com/jobs/42";
const GREENHOUSE_URL = "https://jobs.example.org/fake-b10/1";

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

type Job = { id: string; title: string; url: string; source: string };
type LinkedIn = {
  enabled: boolean;
  accepted: boolean;
  paused_until: string | null;
  needs_signin: boolean;
  last_sweep_at: string | null;
};

let contexts: BrowserContext[] = [];
const pages: Record<string, Page> = {};
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

/** LinkedIn on through the api, as the card's accept button sends it. */
async function turnOn(page: Page, query: string) {
  const answer = await page.request.put("/api/browser/linkedin", {
    headers: CSRF,
    data: { enabled: true, accept_version: 1, query },
  });
  expect(answer.ok(), await answer.text()).toBe(true);
}

/** Find jobs on the jobs page, then the summary's dialog once the sweep ended. */
async function findJobs(page: Page) {
  await page.goto("/jobs");
  await page.getByRole("button", { name: "Find jobs" }).click();
  const setup = page.getByRole("dialog", { name: "Find jobs setup" });
  await expect(setup).toBeVisible();
  await setup.getByRole("button", { name: "Save and Start" }).click();
  const dialog = page.getByRole("dialog", {
    name: /new postings? found|No new posting|The sweep stopped/,
  });
  await expect(dialog).toBeVisible({ timeout: 300_000 });
  return dialog;
}

test.beforeAll(async ({ browser }) => {
  for (const [email, name] of [
    [A, "Alex Example"],
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
    await saveSection(page.request, "skills", () => SKILLS);
    // What the search keeps: the fixture titles, in the fixture places, of any age. A also
    // sweeps the Greenhouse board that lists one of LinkedIn's postings.
    await saveSection(page.request, "search", (search) => ({
      ...search,
      title_include: terms(["ai engineer"]),
      title_exclude: terms([]),
      location: terms(["paris", "london", "milan"]),
      days: 365,
      boards:
        email === A
          ? [
              {
                company: "Example Robotics",
                ats: "greenhouse",
                org: "fake-b10",
                enabled: true,
              },
            ]
          : [],
    }));
    pages[email] = page;
  }
});

test.afterAll(async () => {
  for (const context of contexts) await context.close();
  contexts = [];
  clearAttempts([A, B, C, D]);
});

test("A turns LinkedIn on with its sentence, on a phone", async () => {
  const page = pages[A]!;
  await page.setViewportSize(PHONE);
  await page.goto("/account");
  const card = page.getByRole("region", { name: "LinkedIn", exact: true });
  const toggle = card.getByRole("switch", { name: "LinkedIn search" });
  await expect(toggle).toHaveAttribute("aria-checked", "false");
  await card.getByLabel("Search words").fill("agents");
  await toggle.click();
  const terms = card.getByRole("group", { name: "LinkedIn's terms" });
  await expect(terms.getByText(SENTENCE, { exact: true })).toBeVisible();
  // Nothing wider than the phone.
  const wide = await page.evaluate(
    () => document.documentElement.scrollWidth - window.innerWidth,
  );
  expect(wide).toBeLessThanOrEqual(0);
  await shots(page, "linkedin-sentence");
  await terms
    .getByRole("button", { name: "I accept, turn LinkedIn on" })
    .click();
  await expect(toggle).toHaveAttribute("aria-checked", "true");
  await expect(terms).toBeHidden();
  const state = await linkedinOf(page);
  expect([state.enabled, state.accepted, state.paused_until]).toEqual([
    true,
    true,
    null,
  ]);
  await shots(page, "linkedin-on");
});

test("A's sweep reads LinkedIn: the company's address kept, one row for LinkedIn and Greenhouse", async () => {
  const page = pages[A]!;
  await page.setViewportSize({ width: 1280, height: 800 });
  const dialog = await findJobs(page);
  const sites = dialog.getByRole("region", {
    name: "Job sites in your browser",
  });
  await expect(sites.getByRole("listitem")).toHaveCount(1);
  await expect(sites.getByRole("listitem")).toContainText(
    /^LinkedIn, agents: \d+ postings? found, 2 new\.$/,
  );
  await shots(page, "find-jobs-linkedin", false);
  await dialog.getByRole("button", { name: "Done" }).click();

  const table = page.getByRole("table", { name: "Postings" });
  for (const title of [AGENTS, APPLIED, FORWARD])
    await expect(table).toContainText(title);
  await expect(page.getByRole("main").getByRole("status").first()).toHaveText(
    "3 postings",
  );
  await shots(page, "jobs-from-linkedin");

  const jobs = await jobsOf(page);
  expect(jobs.map((job) => job.title).sort()).toEqual(
    [AGENTS, APPLIED, FORWARD].sort(),
  );
  const byTitle = Object.fromEntries(jobs.map((job) => [job.title, job]));
  // The company's own Apply address is the posting's address.
  expect(byTitle[APPLIED]!.url).toBe(COMPANY_URL);
  // Easy Apply keeps LinkedIn's address.
  expect(byTitle[FORWARD]!.url).toMatch(
    /\/linkedin\/jobs\/view\/4100000103\/$/,
  );
  // Greenhouse and LinkedIn list the same posting: one row naming both.
  expect(byTitle[AGENTS]!.url).toBe(GREENHOUSE_URL);
  expect(byTitle[AGENTS]!.source.split(", ").sort()).toEqual([
    "greenhouse:fake-b10",
    "linkedin:agents",
  ]);
  await table.getByRole("link", { name: AGENTS }).click();
  const main = page.getByRole("main");
  await expect(main).toContainText("greenhouse:fake-b10");
  await expect(main).toContainText("LinkedIn, agents");
  await shots(page, "one-row-two-sources");
});

test("C meets LinkedIn's security check: paused with its date, no second sweep, no apply", async () => {
  const page = pages[C]!;
  await turnOn(page, "checkpoint");
  const dialog = await findJobs(page);
  const sites = dialog.getByRole("region", {
    name: "Job sites in your browser",
  });
  await expect(sites.getByRole("listitem")).toHaveText([
    "LinkedIn, checkpoint: 1 posting found, 1 new. LinkedIn showed a security check, so all LinkedIn work is paused for a week.",
  ]);
  await shots(page, "find-jobs-checkpoint", false);
  await dialog.getByRole("button", { name: "Done" }).click();

  // The pause: 7 days from the check, shown with its date on the card.
  const state = await linkedinOf(page);
  expect(state.paused_until).not.toBeNull();
  const left = Date.parse(state.paused_until!) - Date.now();
  expect(left).toBeGreaterThan(6.9 * 86_400_000);
  expect(left).toBeLessThanOrEqual(7 * 86_400_000);
  const day = await page.evaluate(
    (iso) =>
      new Date(iso).toLocaleDateString(undefined, { dateStyle: "medium" }),
    state.paused_until!,
  );
  await page.goto("/account");
  const card = page.getByRole("region", { name: "LinkedIn", exact: true });
  await expect(card.getByRole("status")).toHaveText(
    `LinkedIn showed a security check, so all LinkedIn work is paused until ${day}.`,
  );
  await shots(page, "linkedin-paused");

  // A second sweep the same day does not search LinkedIn.
  const again = await findJobs(page);
  await expect(again).toHaveAccessibleName("No new posting");
  await expect(
    again
      .getByRole("region", { name: "Job sites in your browser" })
      .getByRole("listitem"),
  ).toHaveText([
    "LinkedIn, checkpoint: 0 postings found, 0 new. LinkedIn showed a security check, so all LinkedIn work is paused for a week. It was not searched.",
  ]);
  await again.getByRole("button", { name: "Done" }).click();

  // The LinkedIn row found before the check: an application on it is blocked by the guard.
  const [row] = (await jobsOf(page)).filter(
    (job) => job.title === BEFORE_CHECK,
  );
  expect(row).toBeDefined();
  expect(row!.url).toMatch(/\/linkedin\/jobs\/view\/4100000201\/$/);
  await page.goto(`/jobs/${row!.id}`);
  const apply = page.getByRole("region", { name: "Apply", exact: true });
  await apply.getByRole("button", { name: "Apply (Review first)" }).click();
  await expect
    .poll(
      async () => {
        const answer = await page.request.get("/api/applications");
        const body = (await answer.json()) as {
          items: {
            posting_id: string;
            state: string;
            error_code: string | null;
          }[];
        };
        const found = body.items.find((item) => item.posting_id === row!.id);
        return found ? `${found.state} ${found.error_code}` : "none";
      },
      { timeout: 120_000, intervals: [5_000] },
    )
    .toBe("blocked linkedin_paused");
  await page.reload();
  await expect(
    page
      .getByRole("region", { name: "Apply", exact: true })
      .getByText("The last attempt was blocked by a rule. Nothing was sent."),
  ).toBeVisible();
  await shots(page, "apply-on-linkedin-blocked");
});

test("D meets LinkedIn's sign in page: told to sign in, no hand off", async () => {
  const page = pages[D]!;
  await turnOn(page, "signin");
  const dialog = await findJobs(page);
  await expect(
    dialog
      .getByRole("region", { name: "Job sites in your browser" })
      .getByRole("listitem"),
  ).toHaveText([
    "LinkedIn, signin: 0 postings found, 0 new. LinkedIn needs you to sign in. Nothing else was opened on LinkedIn.",
  ]);
  await dialog.getByRole("button", { name: "Done" }).click();

  const state = await linkedinOf(page);
  expect([state.needs_signin, state.paused_until]).toEqual([true, null]);
  await page.goto("/account");
  const card = page.getByRole("region", { name: "LinkedIn", exact: true });
  await expect(card.getByRole("status")).toContainText(
    "LinkedIn needs you to sign in",
  );
  await shots(page, "linkedin-needs-signin");
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

test("B's switch stays off and B reads nothing of the others", async () => {
  const page = pages[B]!;
  await page.goto("/account");
  const card = page.getByRole("region", { name: "LinkedIn", exact: true });
  await expect(
    card.getByRole("switch", { name: "LinkedIn search" }),
  ).toHaveAttribute("aria-checked", "false");
  await expect(card.getByRole("status")).toHaveCount(0);
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
