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
 * Jobs J5's sweep, the schedule and the never apply rule on a job website (owner, 2026-10-28:
 * every search, by hand or on the schedule, runs the job websites ticked in Find jobs alone; the
 * boards and feeds of jobs-sweep.spec.ts and opportunities-o7.spec.ts no longer run). Against the
 * real stack through Caddy, its browser, the fake model backend and the invented job website of
 * tests/browser_pages/j15/ (tests/browser-pages.compose.yml), no mocks: never a real job site.
 * The recorded finder (app/agents/fake_streams/browser_discovery.json) searches that website and
 * reports its one posting, Robot Engineer at Example Robotics.
 *
 * - Person A adds the website in Find jobs and starts: the progress comes over the event stream
 *   through Caddy (no polling), Find jobs is disabled while it runs, the summary names the new
 *   posting and fits a phone, the list refreshes, and the history says automatic search is off
 *   and lists the search by hand. A second start while one runs is refused (409) and names the
 *   running one; that search finds the posting again and adds nothing.
 * - A's schedule: off by default, turned on with a time a minute ahead, fires once by itself on
 *   the website, then turned off.
 * - Person C's never apply rule names robotics: the website's posting is stored skipped with
 *   the rule as its note, and never reaches C's deck.
 * - Person B ticked no website: a start is refused with what to do, nothing is queued, and B's
 *   jobs and deck are empty.
 *
 * Run it with the group (web/e2e/browser-all.config.ts says how).
 */
test.describe.configure({ mode: "serial", timeout: 360_000 });
test.skip(!caddyUrl, "set E2E_CADDY_URL with the Compose stack running");

const run = Date.now().toString(36);
const A = `e2e-jobs-j5a-${run}@example.org`;
const B = `e2e-jobs-j5b-${run}@example.org`;
const C = `e2e-jobs-j5c-${run}@example.org`;
const CSRF = { "X-Requested-With": "jobapp" };
const SITE_HOST = "jobs.example-robotics.test";
const SITE = `http://${SITE_HOST}:8080/j15/`;
const POSTING = "Robot Engineer";
const RULE = "No robotics companies: robotics (journey rule)";
const shots = makeShots("jobs-j5-websites");
const tail = () => Math.random().toString(36).slice(2, 12).padEnd(10, "x");
const KEY = `sk-ant-api03-e2ediscovery${tail()}${tail()}Fj5w`;
const terms = (list: string[]) => ({ terms: list, advanced_regex: false });
const WEBSITE = {
  platform: "custom",
  url: SITE,
  enabled: true,
  mode: "llm_browser",
};

type Sweep = {
  id: string;
  status: string;
  trigger: string;
  new: number;
  sources: { kind: string; found: number; new: number }[];
};
type Job = {
  id: string;
  title: string;
  company: string;
  status: string;
  notes: string;
};

const contexts: BrowserContext[] = [];
const pages: Record<string, Page> = {};
const errors: string[][] = [];

/** Model access for the website's finder, and the search: the website (or none) and the rules. */
async function prepare(
  request: APIRequestContext,
  { website, rules }: { website: boolean; rules: string[] },
) {
  const saved = await request.put("/api/me/model-credential", {
    headers: CSRF,
    data: { kind: "anthropic_api_key", secret: KEY },
  });
  expect(saved.status()).toBe(200);
  const profile = (await (await request.get("/api/profile")).json()) as {
    revision: number;
  };
  const search = await request.put("/api/profile/search", {
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
        never_apply: rules,
        interactive_sites: website ? [WEBSITE] : [],
      },
    },
  });
  expect(search.ok(), await search.text()).toBe(true);
}

async function sweeps(request: APIRequestContext) {
  const answer = await request.get("/api/jobs/sweeps");
  expect(answer.ok()).toBe(true);
  return (await answer.json()) as {
    items: Sweep[];
    schedule: { active: boolean; next_run: string | null };
  };
}

async function until(request: APIRequestContext, id: string): Promise<Sweep> {
  let last: Sweep | undefined;
  await expect
    .poll(
      async () => {
        const answer = await request.get(`/api/jobs/sweeps/${id}`);
        last = (await answer.json()) as Sweep;
        return last.status;
      },
      { timeout: 300_000, intervals: [2_000] },
    )
    .toBe("done");
  return last!;
}

async function jobsOf(request: APIRequestContext): Promise<Job[]> {
  const answer = await request.get("/api/jobs?limit=200");
  expect(answer.ok()).toBe(true);
  return ((await answer.json()) as { items: Job[] }).items;
}

async function deckOf(request: APIRequestContext) {
  const answer = await request.get("/api/postings/deck");
  expect(answer.status()).toBe(200);
  return (await answer.json()) as { cards: { id: string; title: string }[] };
}

async function fitsInside(page: Page, dialog: ReturnType<Page["getByRole"]>) {
  for (const width of [1280, 375]) {
    await page.setViewportSize({ width, height: 800 });
    const overflow = await dialog.evaluate((el) => {
      const box = el.getBoundingClientRect();
      let widest = 0;
      for (const child of Array.from(el.querySelectorAll("*")))
        widest = Math.max(
          widest,
          child.getBoundingClientRect().right - box.right,
        );
      return Math.max(widest, el.scrollWidth - el.clientWidth);
    });
    expect(overflow, `dialog at ${width}px`).toBeLessThanOrEqual(1);
  }
  await page.setViewportSize({ width: 1280, height: 800 });
}

/** "HH:MM" in Paris, `minutes` from now. */
function parisClock(minutes: number): string {
  return new Intl.DateTimeFormat("en-GB", {
    timeZone: "Europe/Paris",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
  }).format(new Date(Date.now() + minutes * 60_000));
}

test.beforeAll(async ({ browser }) => {
  for (const [email, name] of [
    [A, "Alex Example"],
    [B, "Blake Example"],
    [C, "Casey Example"],
  ] as const) {
    const context = await newContext(browser);
    contexts.push(context);
    const page = await context.newPage();
    errors.push(watchErrors(page));
    await page.goto(cliLink(email));
    await setPassword(page, `jobs j5 websites ${run} passphrase`, name);
    await prepare(page.request, {
      website: email === C,
      rules: email === C ? [RULE] : [],
    });
    pages[email] = page;
  }
});

test.afterAll(async () => {
  for (const context of contexts) await context.close();
  clearAttempts([A, B, C]);
});

test("Find jobs on a website: live progress, summary, history, list refreshed", async () => {
  const page = pages[A]!;
  await page.goto("/jobs");
  await expect(page.getByRole("main").getByRole("status").first()).toHaveText(
    "0 postings",
  );
  // Progress must come over the event stream through Caddy, not the polling fallback.
  const polls: string[] = [];
  const onRequest = (request: { url: () => string }) => {
    if (
      /\/api\/jobs\/sweeps\/[0-9a-f-]{36}$/.test(
        new URL(request.url()).pathname,
      )
    )
      polls.push(request.url());
  };
  page.on("request", onRequest);
  const stream = page.waitForResponse(
    /\/api\/jobs\/sweeps\/[0-9a-f-]{36}\/events$/,
  );
  await page.getByRole("button", { name: "Find jobs" }).click();
  const setup = page.getByRole("dialog", { name: "Find jobs setup" });
  await setup.getByLabel("Custom website URL").fill(SITE);
  await setup.getByRole("button", { name: "Add website" }).click();
  await expect(setup).toContainText(SITE);
  await setup.getByRole("button", { name: "Save and Start" }).click();
  expect((await stream).headers()["content-type"]).toContain(
    "text/event-stream",
  );

  const progress = page.getByRole("region", { name: "Sweep in progress" });
  await expect(progress).toContainText(`Reading ${SITE_HOST}`, {
    timeout: 60_000,
  });
  await expect(
    page.getByRole("button", { name: "Finding jobs" }),
  ).toBeDisabled();
  await shots(page, "find-jobs-progress", false);

  const dialog = page.getByRole("dialog", { name: "1 new posting found" });
  await expect(dialog).toBeVisible({ timeout: 300_000 });
  page.off("request", onRequest);
  expect(polls).toEqual([]);
  await expect(dialog).toContainText("1 website searched");
  const links = dialog
    .getByRole("region", { name: "New postings" })
    .getByRole("link");
  await expect(links).toHaveCount(1);
  await expect(links).toContainText(POSTING);
  await expect(dialog).not.toContainText(/board/i);
  await expect(dialog).not.toContainText(/[–—]| - /);
  await fitsInside(page, dialog);
  await shots(page, "find-jobs-summary", false);

  // The list refreshed behind the dialog.
  await dialog.getByRole("button", { name: "Done" }).click();
  await expect(page.getByRole("main").getByRole("status").first()).toHaveText(
    "1 posting",
  );
  await expect(page.getByRole("table", { name: "Postings" })).toContainText(
    POSTING,
  );

  // The history: automatic search is off for every account until its owner turns it on.
  await page.getByRole("button", { name: "Sweep history" }).click();
  const history = page.getByRole("dialog", { name: "Sweeps" });
  await expect(
    history.getByRole("region", { name: "Automatic search" }),
  ).toContainText("Automatic search: off.");
  await expect(
    history.getByRole("list", { name: "Last sweeps" }),
  ).toContainText(/By hand, Done/);
  await fitsInside(page, history);
  await shots(page, "sweep-history", false);
  await history.getByRole("button", { name: "Close" }).click();

  // A second start at once is refused; the running one is followed instead.
  const first = await page.request.post("/api/jobs/sweeps", { headers: CSRF });
  expect(first.status(), await first.text()).toBe(202);
  const second = await page.request.post("/api/jobs/sweeps", {
    headers: CSRF,
  });
  expect(second.status()).toBe(409);
  const running = ((await first.json()) as { id: string }).id;
  expect(((await second.json()) as { sweep_id: string }).sweep_id).toBe(
    running,
  );
  const rerun = await until(page.request, running);
  expect(rerun.new).toBe(0);
  expect(rerun.sources.map((source) => source.kind)).toEqual(["custom"]);
  expect(await jobsOf(page.request)).toHaveLength(1);
  expect(errors[0]).toEqual([]);
});

test("Schedule: off by default, turned on, fires at its time on the website, turned off", async () => {
  test.setTimeout(600_000);
  const page = pages[A]!;
  await page.goto("/jobs");
  const button = page.getByRole("button", { name: /^Schedule/ });
  await expect(button).toContainText("Off");
  await button.click();
  const dialog = page.getByRole("dialog", { name: "Automatic search" });
  await expect(dialog.getByRole("status")).toHaveText(
    "Automatic search is off.",
  );
  await expect(dialog).not.toContainText(/board/i);
  await dialog.getByRole("checkbox", { name: "Search automatically" }).check();
  // The worker's next tick after this time fires it (every minute with TEST_FAST_TIMERS, which
  // tests/ats-stub.compose.yml sets on every journey stack; every 5 minutes otherwise).
  const time = parisClock(1);
  await dialog.getByLabel("Time 1", { exact: true }).fill(time);
  await expect(dialog.getByRole("status")).toContainText(
    "Next automatic search: ",
  );
  await fitsInside(page, dialog);
  await shots(page, "schedule-dialog", false);
  await page.getByRole("button", { name: "Save" }).click();
  await expect(dialog).toBeHidden();
  await expect(button).toContainText("On");
  const listed = await sweeps(page.request);
  expect(listed.schedule.active).toBe(true);
  const before = listed.items.length;

  // The worker's tick starts it by itself, once, as a schedule sweep, on the website alone.
  await expect
    .poll(
      async () => {
        const body = await sweeps(page.request);
        const automatic = body.items.filter((i) => i.trigger === "schedule");
        return automatic.length
          ? `${automatic.length} ${automatic[0]!.status}`
          : "none";
      },
      { timeout: 420_000, intervals: [5_000] },
    )
    .toBe("1 done");
  const body = await sweeps(page.request);
  expect(body.items.length).toBe(before + 1);
  const automatic = body.items.find((i) => i.trigger === "schedule")!;
  const fired = (await (
    await page.request.get(`/api/jobs/sweeps/${automatic.id}`)
  ).json()) as Sweep;
  expect(fired.sources.map((source) => source.kind)).toEqual(["custom"]);

  // Off again.
  await button.click();
  await dialog
    .getByRole("checkbox", { name: "Search automatically" })
    .uncheck();
  await page.getByRole("button", { name: "Save" }).click();
  await expect(button).toContainText("Off");
  expect((await sweeps(page.request)).schedule.active).toBe(false);
});

test("C's never apply rule: the website's posting is stored skipped and never in the deck", async () => {
  const page = pages[C]!;
  const started = await page.request.post("/api/jobs/sweeps", {
    headers: CSRF,
  });
  expect(started.status(), await started.text()).toBe(202);
  const done = await until(
    page.request,
    ((await started.json()) as { id: string }).id,
  );
  expect(done.sources.map((source) => [source.kind, source.found])).toEqual([
    ["custom", 1],
  ]);
  const rows = await jobsOf(page.request);
  expect(
    rows.map((row) => [row.title, row.company, row.status, row.notes]),
  ).toEqual([[POSTING, "Example Robotics", "skipped", RULE]]);
  const deck = await deckOf(page.request);
  expect(deck.cards.some((card) => card.title === POSTING)).toBe(false);
  await page.goto("/discover");
  await expect(page.getByTestId("deck-card")).toHaveCount(0);
  await expect(page.getByRole("main")).not.toContainText(POSTING);
});

test("B ticked no website: the start is refused, nothing queued, nothing of A or C", async () => {
  const page = pages[B]!;
  const refused = await page.request.post("/api/jobs/sweeps", {
    headers: CSRF,
  });
  expect(refused.status()).toBe(422);
  expect(await refused.text()).toContain(
    "Tick at least one job website or job feed in Find jobs, then start the search again.",
  );
  expect((await sweeps(page.request)).items).toEqual([]);
  expect(await jobsOf(page.request)).toEqual([]);
  expect((await deckOf(page.request)).cards).toEqual([]);
});

test("no page error on any side", () => {
  for (const seen of errors) expect(seen).toEqual([]);
});
