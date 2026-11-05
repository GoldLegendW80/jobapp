import { execFileSync } from "node:child_process";

import { expect, test, type BrowserContext, type Page } from "@playwright/test";

import { PAGES } from "./browser-b7-helpers";
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
 * Jobs J15: Find jobs searches the person's chosen websites in the person's browser, against
 * the real stack through Caddy, its browser, the fake model backend and the static pages of
 * tests/browser_pages/ (tests/browser-pages.compose.yml), no mocks: never a real job site.
 *
 * - Person A opens Find jobs, chooses the four job websites by name (LinkedIn, Welcome to the
 *   Jungle, Indeed, Free-Work) from the examples and a website of her own, the invented job website of
 *   tests/browser_pages/j15/ (http://jobs.example-robotics.test:8080/j15/, the pages service's
 *   alias). Each job website shows its risk sentence in its line of the list: adding it and saving is
 *   her permission to search it (owner, 2026-10-27, jobs J17), so no permission prompt or card
 *   follows, and the save records the four permissions. She sets the criteria (engineer,
 *   Paris) and instructions, then Save and start search.
 * - A website's run page (browser B18) shows the live view of the browser while that website is
 *   read, watching, with Take over, at 375 px and 1280 px, light and dark.
 * - The progress names the website being read. On her own website the interactive finder
 *   (app/agents/fake_streams/browser_discovery.json) types the search words, picks Paris,
 *   presses Search, opens the second page of results and the posting: the site answers that
 *   posting only to that path (server.py, /j15/search). The posting's page asks the assistant
 *   to search other words, open an address and press its Send application button; the recorded
 *   model does all three. Since B20 (owner, 2026-11-04: the search works as Claude Code with
 *   Playwright CLI) the agent may type any words and open any public address, so only the line
 *   holds: browserd refuses the click on the button of a POST form, and the site sees no POST.
 *   The website, which gave a verified posting, was searched (2026-10-28: how it ends is what
 *   was read, no error).
 *   The posting, verified on its page (title, employer and location read there), is saved to
 *   her list as it is reported (jobs J11): found 1, new 1, and it is in the postings list.
 *   The site sees GET requests only, none for the address, the profile keeps its revision.
 * - The company careers page of browser B9 (tests/browser_pages/b9/), sent by an older tab, is
 *   not refused but is not stored, shown in the setup nor searched: the fixed readers were
 *   deleted and every search runs the ticked websites alone (owner, 2026-10-28). The setup has
 *   no regular expression switch.
 * - No pacing: the search needs no pause between pages (B20), so the journey asserts the order of
 *   the site's requests and no gap. The agent of a website has no page cap of its own (owner, 2026-10-27, jobs J18): its one run reads the
 *   pages its searches need. The summary names the search it ran, from its own tool calls.
 * - Reopened at 375 px in the dark, by keyboard, the setup holds her websites and criteria.
 *
 * What the stack does not do yet, so this journey asserts the current truth: the four job
 * websites, each with its permission, pass the search's permission check (browser B14) and open
 * the pages service instead of their real host; no fake of their search exists yet.
 *
 * Run it with the group (web/e2e/browser-all.config.ts says how).
 */
test.describe.configure({ mode: "serial", timeout: 360_000 });
test.skip(!caddyUrl, "set E2E_CADDY_URL with the Compose stack running");

const run = Date.now().toString(36);
const A = `e2e-jobs-j15a-${run}@example.org`;
const CSRF = { "X-Requested-With": "jobapp" };
const SITE_HOST = "jobs.example-robotics.test";
const SITE = `http://${SITE_HOST}:8080/j15/`;
const COMPANY = `${PAGES}/b9/careers.html`;
const WEBSITES = ["LinkedIn", "Welcome to the Jungle", "Indeed", "Free-Work"];
// The consent route of each job website (jobs J17 records the permission through it).
const PERMISSIONS = {
  linkedin: "/api/browser/linkedin",
  welcome_to_the_jungle: "/api/opportunities/platforms/welcome_to_the_jungle",
  indeed: "/api/opportunities/platforms/indeed",
  freework: "/api/opportunities/platforms/freework",
};
const INSTRUCTIONS = "Prefer teams that build robots.";
const shots = makeShots("jobs-j15");
const tail = () => Math.random().toString(36).slice(2, 12).padEnd(10, "x");
const KEY = `sk-ant-api03-e2ediscovery${tail()}${tail()}Fj15`;
const terms = (list: string[]) => ({ terms: list, advanced_regex: false });

type Source = {
  kind: string;
  mode?: string | null;
  host?: string | null;
  found: number;
  new: number;
  result_pages: number;
  error: string | null;
  outcome?: string | null;
  searches?: number;
  searched?: { title: string; place: string | null }[];
};
type Sweep = { id: string; status: string; new: number; sources: Source[] };
type Seen = { method: string; path: string; at: number };

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

/** What the invented job website saw, read inside the pages container. */
function siteLog(): Seen[] {
  const read =
    "import urllib.request;print(urllib.request.urlopen('http://127.0.0.1:8080/j15/log').read().decode())";
  return JSON.parse(
    compose("exec", "-T", "pages", "python", "-c", read),
  ) as Seen[];
}

/** The address the browser container gives a name (its proxy resolves through the same DNS). */
function addressOf(name: string): string {
  const look = `import socket;print(socket.gethostbyname('${name}'))`;
  return compose("exec", "-T", "browser", "python", "-c", look).trim();
}

let context: BrowserContext;
let page: Page;
let errors: string[] = [];
let before = 0;
let revision = 0;

test.beforeAll(async ({ browser }) => {
  // Fail closed: the website's name must lead to the pages service, never anywhere else.
  expect(addressOf(SITE_HOST)).toBe(addressOf("pages"));
  context = await newContext(browser);
  page = await context.newPage();
  errors = watchErrors(page, []);
  await page.goto(cliLink(A));
  await setPassword(page, `jobs j15 journey ${run} passphrase`, "Alex Example");
  // What A did before in Account and Search options: model access for the finder, her titles
  // and, as an older tab still sends it, the company careers page of browser B9.
  const saved = await page.request.put("/api/me/model-credential", {
    headers: CSRF,
    data: { kind: "anthropic_api_key", secret: KEY },
  });
  expect(saved.status()).toBe(200);
  const profile = await page.request.get("/api/profile");
  const current = (await profile.json()) as { revision: number };
  const search = await page.request.put("/api/profile/search", {
    headers: CSRF,
    data: {
      base_revision: current.revision,
      data: {
        title_include: terms(["engineer"]),
        title_exclude: terms([]),
        location: terms([]),
        contract_types: [],
        remote_stance: "any",
        min_fit: 70,
        days: 365,
        never_apply: [],
        browser_sources: [{ kind: "site", query: COMPANY, enabled: true }],
      },
    },
  });
  expect(search.ok(), await search.text()).toBe(true);
  before = siteLog().length;
});

test.afterAll(async () => {
  await context.close();
});

test("A adds websites to the list beside their risk sentences, sets criteria and starts Find jobs", async () => {
  await page.goto("/jobs");
  await page.getByRole("button", { name: "Find jobs" }).click();
  const setup = page.getByRole("dialog", { name: "Find jobs" });
  await expect(setup).toBeVisible();

  const list = setup.getByRole("list", { name: "Your list" });
  const line = (name: string) =>
    list.getByRole("listitem").filter({
      has: page.getByRole("button", { name: `Remove ${name}`, exact: true }),
    });
  for (const name of WEBSITES) {
    await setup
      .getByRole("button", { name: `Add ${name}`, exact: true })
      .click();
    await expect(line(name)).toBeVisible();
  }
  await setup.getByLabel("Website address").fill(SITE);
  await setup.getByRole("button", { name: "Add", exact: true }).click();
  await expect(list).toContainText(SITE_HOST);

  // Each job website's risk sentence is in its line, and adding it is the permission:
  // no prompt, card or second step.
  for (const name of WEBSITES) {
    await expect(line(name)).toContainText(/risk/);
    await expect(line(name)).toContainText(
      "Saving with this website in your list gives this permission.",
    );
  }
  // LinkedIn's one switch also covers Easy Apply, and its line says so (owner, 2026-10-27).
  await expect(line("LinkedIn")).toContainText(/also allows Easy Apply/);
  await expect(
    setup.getByRole("button", { name: "Review website permissions" }),
  ).toHaveCount(0);
  await expect(setup.getByRole("checkbox", { name: /I accept/ })).toHaveCount(
    0,
  );

  await expect(
    setup.getByRole("list", { name: "Terms of Job titles" }),
  ).toContainText("engineer");
  await setup
    .getByRole("textbox", { name: "New term for Places" })
    .fill("Paris");
  await setup
    .getByRole("textbox", { name: "New term for Places" })
    .press("Enter");
  await expect(
    setup.getByRole("list", { name: "Terms of Places" }),
  ).toContainText("Paris");
  await setup.getByRole("button", { name: "More options" }).click();
  await setup.getByLabel("Instructions for the assistant").fill(INSTRUCTIONS);
  // The journey follows the browser agent on LinkedIn's fixture pages: LinkedIn's public job
  // search is the default (owner, 2026-10-30), so it chooses the browser.
  await setup
    .getByRole("radio", { name: "Signed in, in your own browser" })
    .check();
  // Owner, 2026-10-28: the setup is the websites and the words. The company careers page she
  // sent (a deleted reader) is neither shown nor searched, and no switch asks for regular
  // expressions.
  await expect(
    setup.getByRole("region", { name: "Other search sources" }),
  ).toHaveCount(0);
  await expect(setup.getByText(COMPANY)).toHaveCount(0);
  await expect(
    setup.getByRole("checkbox", { name: "Websites only" }),
  ).toHaveCount(0);
  await expect(
    setup.getByRole("switch", { name: /regular expressions/i }),
  ).toHaveCount(0);
  await shots(page, "setup");
  await setup.getByRole("button", { name: "Save and start search" }).click();
  await expect(setup).toBeHidden({ timeout: 30_000 });
  // The save recorded each listed website's permission through its consent route.
  for (const [kind, path] of Object.entries(PERMISSIONS)) {
    const permission = await page.request.get(path);
    expect(permission.ok(), kind).toBe(true);
    expect(
      ((await permission.json()) as { allowed: boolean }).allowed,
      kind,
    ).toBe(true);
  }
  revision = (
    (await (await page.request.get("/api/profile")).json()) as {
      revision: number;
    }
  ).revision;

  // browser B18: a website's run page shows the browser the search reads. The first website's
  // run is the first to read a website, so it is still running when its
  // page is open; the view connects on the sweep lease and only watches (no Take over is
  // clicked, nothing here starts or ends a lease).
  let websiteRun = "";
  await expect
    .poll(
      async () => {
        const answer = await page.request.get("/api/agents/runs?limit=50");
        const { items } = (await answer.json()) as {
          items: { id: string; agent: string; status: string }[];
        };
        websiteRun =
          items.find(
            (item) =>
              item.agent === "browser_discovery" && item.status === "running",
          )?.id ?? "";
        return websiteRun;
      },
      { timeout: 120_000, intervals: [1_000] },
    )
    .not.toBe("");
  const runPage = await context.newPage();
  await runPage.goto(`/runs/${websiteRun}`);
  const live = runPage.getByTestId("run-live-view");
  await expect(live.getByTestId("live-screen").locator("canvas")).toBeVisible({
    timeout: 60_000,
  });
  await expect(
    live.getByText(
      "You are watching your browser. Take over to use it yourself.",
    ),
  ).toBeVisible();
  await expect(
    live.getByRole("button", { name: "Take over", exact: true }),
  ).toBeEnabled();
  await expect(runPage.getByText("Your browser is starting.")).toHaveCount(0);
  // The canvas holds a frame of the browser (more than one colour), as b4 and b12 wait for it.
  await expect
    .poll(
      () =>
        live.getByTestId("live-screen").evaluate((screen) => {
          const canvas = screen.querySelector("canvas");
          const context = canvas?.getContext("2d");
          if (!canvas || !context || canvas.width === 0) return false;
          const { data } = context.getImageData(
            0,
            0,
            canvas.width,
            canvas.height,
          );
          for (let i = 4; i < data.length; i += 4 * 97) {
            for (let c = 0; c < 3; c += 1)
              if (data[i + c] !== data[c]) return true;
          }
          return false;
        }),
      { timeout: 30_000 },
    )
    .toBe(true);
  await shots(runPage, "run-live-view", false);
  await runPage.close();

  // The progress names the website being read, never an internal id. Her website comes after
  // the four ticked job websites, one agent each (J17, J18).
  const progress = page.getByRole("region", { name: "Search in progress" });
  await expect(progress).toContainText(`Reading ${SITE_HOST}`, {
    timeout: 300_000,
  });
  await shots(page, "progress", false);
});

test("the websites end in words, the company page is neither kept nor searched, nothing is submitted", async () => {
  // The summary the page opens when the search ends names every website and says why each
  // ended as it did.
  const summary = page.getByRole("dialog", { name: /new posting/ });
  await expect(summary).toBeVisible({ timeout: 300_000 });
  const sites = summary.getByRole("region", {
    name: "Job sites in your browser",
  });
  // The search checks each website's permission (#631): the four ticked ones pass it.
  for (const name of WEBSITES) {
    const line = sites.getByRole("listitem").filter({ hasText: `${name}:` });
    await expect(line).not.toContainText("needs your permission");
  }
  // Searched: the refused click on the posting's button is counted, never the website's end
  // (2026-10-28), so its line says what was found and nothing else.
  const ownLine = sites
    .getByRole("listitem")
    .filter({ hasText: `${SITE_HOST}:` });
  await expect(ownLine).toContainText(
    "1 search ran: engineer in Paris. 1 posting found, 1 new.",
  );
  await expect(ownLine).not.toContainText("The website asked for something");
  await expect(summary).not.toContainText(/[–—]| - /);
  await shots(page, "summary");
  await page.keyboard.press("Escape");
  await expect(summary).toBeHidden();

  let last: Sweep | undefined;
  await expect
    .poll(
      async () => {
        const answer = await page.request.get("/api/jobs/sweeps?limit=1");
        last = ((await answer.json()) as { items: Sweep[] }).items[0];
        return last?.status;
      },
      { timeout: 300_000, intervals: [2_000] },
    )
    .toBe("done");
  const sources = last?.sources ?? [];
  const detail = JSON.stringify(sources);

  // The four job websites, ticked and named. The search reads each one's permission (#631),
  // recorded by the save: each passes the check and opens its first page. Under
  // BROWSER_SOURCES_BASE_URL they open the pages service's /b8/<platform>/, never the real
  // host; what that page holds is not asserted here (no fake of their search yet).
  for (const kind of Object.keys(PERMISSIONS)) {
    const source = sources.find((item) => item.kind === kind);
    expect(source, detail).toMatchObject({ mode: "llm_browser" });
    expect(source?.error ?? "", detail).not.toMatch(/^permission_/);
    expect(source?.result_pages ?? 0, detail).toBeGreaterThanOrEqual(1);
  }
  // Her own website: searched through the finder's tools, the posting verified and saved
  // (J11). The click on the posting's Send application button was refused and counted; the
  // website gave a verified posting, so it was searched (no error, 2026-10-28).
  const own = sources.find((item) => item.kind === "custom");
  expect(own, detail).toMatchObject({
    mode: "llm_browser",
    host: SITE_HOST,
    found: 1,
    new: 1,
    error: null,
    // One run for the website (jobs J18): the search its tool calls ran, a saved title typed
    // and a saved place chosen.
    searches: 1,
    searched: [{ title: "engineer", place: "Paris" }],
  });
  expect(own?.result_pages ?? 0).toBeGreaterThanOrEqual(4);
  // The company careers page (browser B9) is not searched and was not stored: its reader was
  // deleted with the boards, and every search runs the ticked websites alone (owner, 2026-10-28).
  expect(
    sources.find((item) => item.kind === "site"),
    detail,
  ).toBeUndefined();
  const saved = (await (await page.request.get("/api/profile")).json()) as {
    sections: { search: { browser_sources: Array<{ query: string }> } };
  };
  expect(
    saved.sections.search.browser_sources.map((row) => row.query),
  ).not.toContain(COMPANY);

  // What the site saw: the person's words and place only, the posting reached by the second
  // page of results, then the posting; the address the page asked for is a public GET and may
  // be opened (B20), but the refused click on its button sent no POST.
  const seen = siteLog().slice(before);
  const paths = seen.map((item) => item.path);
  expect(paths.slice(0, 4)).toEqual([
    "/j15/",
    "/j15/search?q=engineer&location=Paris",
    "/j15/search?q=engineer&location=Paris&page=2",
    "/j15/jobs/robot-engineer.html",
  ]);
  expect(seen.every((item) => item.method === "GET")).toBe(true);
  expect(seen.some((item) => item.method === "POST")).toBe(false);
  // No pause is required between pages (B20): the order above is all that is asserted.
  // The page's request to change the profile changed nothing.
  const after = (await (await page.request.get("/api/profile")).json()) as {
    revision: number;
  };
  expect(after.revision).toBe(revision);

  // The postings list, after the summary: the job website's posting, found, with the employer
  // its page names and the website as its source. The company page was not searched, so its
  // posting is not there.
  await page.goto("/jobs");
  const main = page.getByRole("main");
  await expect(main).toContainText("Robot Engineer");
  await expect(main).not.toContainText("AI Engineer");
  const listed = await page.request.get("/api/jobs?limit=200");
  expect(listed.ok()).toBe(true);
  const robot = (
    (await listed.json()) as {
      items: {
        title: string;
        company: string;
        source: string | null;
        status: string;
        url: string | null;
      }[];
    }
  ).items.find((item) => item.title === "Robot Engineer");
  expect(robot).toMatchObject({
    company: "Example Robotics",
    source: SITE_HOST,
    status: "found",
    url: `${SITE}jobs/robot-engineer.html`,
  });
  await expect(page.locator("body")).not.toContainText(/[–—]| - /);
  expect(errors).toEqual([]);
});

test("reopened by keyboard at 375 px in the dark, the setup holds her choices", async () => {
  await page.setViewportSize({ width: 375, height: 812 });
  await page.emulateMedia({ colorScheme: "dark" });
  await page.goto("/jobs");
  const open = page.getByRole("button", { name: "Find jobs" });
  await open.focus();
  await page.keyboard.press("Enter");
  const setup = page.getByRole("dialog", { name: "Find jobs" });
  await expect(setup).toBeVisible();
  for (const name of WEBSITES)
    await expect(
      setup.getByRole("button", { name: `Remove ${name}`, exact: true }),
    ).toBeVisible();
  await expect(setup).toContainText(SITE_HOST);
  await expect(
    setup.getByRole("list", { name: "Terms of Places" }),
  ).toContainText("Paris");
  await setup.getByRole("button", { name: "More options" }).click();
  await expect(setup.getByLabel("Instructions for the assistant")).toHaveValue(
    INSTRUCTIONS,
  );
  const wide = await page.evaluate(() => document.documentElement.scrollWidth);
  expect(wide).toBeLessThanOrEqual(375);
  await page.keyboard.press("Escape");
  await expect(setup).toBeHidden();
  await expect(open).toBeFocused();
  // Closing the setup started nothing: the last sweep is still the one above.
  const answer = await page.request.get("/api/jobs/sweeps?limit=1");
  expect(((await answer.json()) as { items: Sweep[] }).items[0]?.status).toBe(
    "done",
  );
  expect(siteLog().length - before).toBe(5);
  await page.emulateMedia({ colorScheme: "light" });
  expect(errors).toEqual([]);
});
