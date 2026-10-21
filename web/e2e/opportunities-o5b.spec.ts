import {
  expect,
  test,
  type BrowserContext,
  type Locator,
  type Page,
} from "@playwright/test";

import {
  CSRF,
  caddyUrl,
  invented,
  signUp,
  type Person,
} from "./documents-helpers";
import { clearAttempts, makeShots, newContext, watchErrors } from "./stack";

/**
 * Opportunities O5b against the real stack through Caddy: the starter list of boards and sites
 * on the wizard's summary. The stack sends every ATS call to the fake ATS server
 * (tests/ats-stub.compose.yml), so the api proposes from its stub catalog
 * (api/app/opportunities/starter_stub.json): invented companies on the fake server's orgs, never
 * a real board.
 *
 * An invented admin invites two invented members, N and B. N's roles (AI engineer, in Paris) are
 * saved, then N opens the summary at phone width: the list proposes the fake boards and Google
 * Careers and Business France VIE (always proposed) with nothing ticked. N removes both sources and two boards, ticks one board and adds
 * it, then adds the rest with "Add all"; the summary's count follows. After a reload the boards
 * show as added and the removed entries are never proposed again. Start runs the first sweep on
 * the two fake boards and Discover shows a card. B looks for a role the catalog knows nothing of:
 * the list says so and offers Search options to add a board by its address. B then names AI roles
 * and a never apply rule: B's own list proposes what N removed, never the board B's rule names,
 * and nothing of N's choices. No screen shows a dash.
 *
 *   E2E_CADDY_URL=... E2E_ATS_FORMS_URL=... E2E_COMPOSE_PROJECT=... \
 *     pnpm --dir web exec playwright test -c e2e/opportunities-all.config.ts opportunities-o5b.spec.ts
 */
test.describe.configure({ mode: "serial" });
test.skip(!caddyUrl, "set E2E_CADDY_URL with the Compose stack running");

const DASH = /[‒–—―]| - /;
const PASSWORD = "Starter-journey-2026!";
const STARTED =
  "Your first search has started. New postings appear as it finds them.";
const shotsAt = makeShots("opportunities-o5b", "o5b");
async function shots(page: Page, name: string) {
  await shotsAt(page, name);
  await page.setViewportSize({ width: 375, height: 740 });
}

/** Skills the fake boards' postings name ("Python, agents, LLM evaluation"). */
const SKILLS = {
  years_experience: null,
  have: [
    { name: "python", terms: ["python"] },
    { name: "agents", terms: ["agent", "agents"] },
    { name: "llm", terms: ["llm", "evaluation"] },
  ],
  adjacent: [],
  none: [],
  gates: [],
};

let admin: Person;
let adminContext: BrowserContext;
let adminPage: Page;
const run = `${Date.now().toString(36)}${Math.random().toString(36).slice(2, 6)}`;
const N = { email: `e2e-o5b-new-${run}@example.org`, name: "Robin Sampleton" };
const B = {
  email: `e2e-o5b-other-${run}@example.org`,
  name: "Alex Placeholder",
};
let contextN: BrowserContext;
let pageN: Page;
let contextB: BrowserContext;
let pageB: Page;
let errors: string[];

async function noDash(page: Page) {
  const text = await page.evaluate(() => document.body.innerText);
  expect(text).not.toMatch(DASH);
}

async function putSection(
  page: Page,
  section: string,
  change: (stored: Record<string, unknown>) => Record<string, unknown>,
) {
  const read = await page.request.get("/api/profile");
  const profile = (await read.json()) as {
    revision: number;
    sections: Record<string, Record<string, unknown> | null>;
  };
  const saved = await page.request.put(`/api/profile/${section}`, {
    headers: CSRF,
    data: {
      base_revision: profile.revision,
      data: change(profile.sections[section] ?? {}),
    },
  });
  expect(saved.status(), await saved.text()).toBe(200);
}

const terms = (...values: string[]) => ({
  terms: values,
  advanced_regex: false,
});

async function invite(email: string): Promise<string> {
  const made = await adminPage.request.post("/api/admin/invites", {
    headers: CSRF,
    data: { email },
  });
  expect(made.status()).toBe(201);
  return ((await made.json()) as { path: string }).path;
}

async function accept(page: Page, link: string, name: string) {
  await page.goto(link);
  await page.getByLabel("Name").fill(name);
  await page.getByLabel(/^(New password|Password)$/).fill(PASSWORD);
  await page.getByLabel("Confirm password").fill(PASSWORD);
  await page.getByLabel("Confirm password").press("Enter");
  await expect(page.locator("html[data-onboarding]")).toHaveCount(1);
}

async function press(target: Locator, key: "Enter" | "Space") {
  await target.focus();
  await target.press(key);
}

const starterList = (page: Page) =>
  page.getByRole("region", { name: "Boards and sites to search" });

async function summary(page: Page) {
  await page.goto("/welcome?step=summary");
  await expect(
    page.getByRole("heading", { level: 2, name: /^Step 7 of 7/ }),
  ).toBeVisible();
  const list = starterList(page);
  const advanced = list.getByRole("button", { name: "Advanced" });
  if ((await advanced.getAttribute("aria-expanded")) === "false")
    await advanced.click();
  return list;
}

async function boardsOf(page: Page) {
  const read = await page.request.get("/api/profile");
  const body = (await read.json()) as {
    sections: { search: { boards: Array<{ org: string; enabled: boolean }> } };
  };
  return body.sections.search.boards.map((b) => `${b.org}:${b.enabled}`);
}

test.beforeAll(async ({ browser }) => {
  admin = invented("o5b");
  adminContext = await newContext(browser);
  adminPage = await adminContext.newPage();
  contextN = await newContext(browser);
  pageN = await contextN.newPage();
  await pageN.setViewportSize({ width: 375, height: 740 });
  errors = watchErrors(pageN);
  contextB = await newContext(browser);
  pageB = await contextB.newPage();
});

test.afterAll(async () => {
  await Promise.all([adminContext.close(), contextN.close(), contextB.close()]);
  clearAttempts([admin.email, N.email, B.email]);
});

test("N's summary proposes the fake boards and Google Careers, nothing ticked", async () => {
  await signUp(adminPage, admin);
  await accept(pageN, await invite(N.email), N.name);
  await expect(pageN).toHaveURL(/\/welcome$/);
  await putSection(pageN, "skills", () => SKILLS);
  await putSection(pageN, "search", (stored) => ({
    ...stored,
    title_include: terms("AI engineer"),
    location: terms("Paris"),
    remote_stance: "hybrid",
    contract_types: ["CDI"],
  }));
  const list = await summary(pageN);
  for (const company of [
    "Example Robotics",
    "Sample Analytics",
    "Example Defence Systems",
    "Placeholder Health",
    "Google",
    "Business France VIE",
  ])
    await expect(list.getByText(company, { exact: true })).toBeVisible();
  await expect(list.getByText("Invented Travel")).toHaveCount(0); // hires in Spain only
  await expect(list).toContainText(
    'Google Careers, searched for "AI engineer" in Paris',
  );
  for (const box of await list.getByRole("checkbox").all())
    await expect(box).not.toBeChecked();
  await expect(
    list.getByRole("button", { name: "Add the ticked ones" }),
  ).toBeDisabled();
  await expect(pageN.getByText(/covers 0 boards from/)).toBeVisible();
  expect(await boardsOf(pageN)).toEqual([]);
  await noDash(pageN);
  await shots(pageN, "starter-list");
});

test("N removes four entries, adds one ticked board, then adds the rest", async () => {
  const list = starterList(pageN);
  for (const company of [
    "Google",
    "Business France VIE",
    "Example Defence Systems",
    "Placeholder Health",
  ]) {
    await press(
      list.getByRole("button", { name: `Remove ${company} from the list` }),
      "Enter",
    );
    await expect(list.getByText(company, { exact: true })).toHaveCount(0);
  }
  await press(
    list.getByRole("checkbox", { name: /Example Robotics/ }),
    "Space",
  );
  await press(
    list.getByRole("button", { name: "Add the ticked ones" }),
    "Enter",
  );
  await expect(
    list.getByText("Added 1 board or site to Search options."),
  ).toBeVisible();
  await expect(pageN.getByText(/covers 1 board from/)).toBeVisible();
  await press(list.getByRole("button", { name: "Add all" }), "Enter");
  await expect(list.getByText("Added", { exact: true })).toHaveCount(2);
  await expect(pageN.getByText(/covers 2 boards from/)).toBeVisible();
  expect(await boardsOf(pageN)).toEqual(["fake-ok:true", "fake-ok2:true"]);
  await noDash(pageN);
  await shots(pageN, "starter-added");
});

test("after a reload the boards show as added and the removed ones never come back", async () => {
  const list = await summary(pageN);
  await expect(list.getByText("Added", { exact: true })).toHaveCount(2);
  for (const company of [
    "Google",
    "Business France VIE",
    "Example Defence Systems",
    "Placeholder Health",
  ])
    await expect(list.getByText(company, { exact: true })).toHaveCount(0);
  await expect(list.getByRole("button", { name: "Add all" })).toBeDisabled();
  // nor through the api
  const again = await pageN.request.post("/api/opportunities/starter", {
    headers: CSRF,
    data: { add: ["lever:fake-404", "source:google_careers"], remove: [] },
  });
  expect(again.status()).toBe(200);
  expect(((await again.json()) as { added: string[] }).added).toEqual([]);
});

test("Start sweeps the two boards and Discover shows a card", async () => {
  await press(pageN.getByRole("button", { name: "Start" }), "Enter");
  await expect(pageN.getByText(STARTED)).toBeVisible();
  await expect(pageN).toHaveURL(/\/discover$/);
  await expect
    .poll(
      async () => {
        const read = await pageN.request.get("/api/jobs/sweeps?limit=1");
        const body = (await read.json()) as {
          items: Array<{ status: string }>;
        };
        return body.items[0]?.status ?? "none";
      },
      { timeout: 90_000, intervals: [1_000] },
    )
    .toBe("done");
  await pageN.reload();
  await expect(pageN.getByTestId("deck-card").first()).toBeVisible();
  expect(errors).toEqual([]);
});

test("B's roles match nothing: the list says so and offers Search options", async () => {
  await accept(pageB, await invite(B.email), B.name);
  await putSection(pageB, "search", (stored) => ({
    ...stored,
    title_include: terms("Nurse"),
  }));
  const list = await summary(pageB);
  await expect(
    list.getByText(
      "No starter board matches your answers. None is listed for Nurse.",
    ),
  ).toBeVisible();
  const link = list.getByRole("link", { name: "Search options" });
  await expect(link).toHaveAttribute("href", "/profile?tab=search");
  await noDash(pageB);
  await shots(pageB, "starter-nothing");
});

test("B's own list: what N removed is proposed, never the board of B's rule", async () => {
  await putSection(pageB, "search", (stored) => ({
    ...stored,
    title_include: terms("AI engineer"),
    never_apply: ["Defence: defence, defense"],
  }));
  const list = await summary(pageB);
  await expect(list.getByText("Google", { exact: true })).toBeVisible();
  await expect(
    list.getByText("Business France VIE", { exact: true }),
  ).toBeVisible();
  await expect(
    list.getByText("Placeholder Health", { exact: true }),
  ).toBeVisible();
  await expect(list.getByText("Example Defence Systems")).toHaveCount(0);
  await expect(list.getByText("Added", { exact: true })).toHaveCount(0);
  const refused = await pageB.request.post("/api/opportunities/starter", {
    headers: CSRF,
    data: { add: ["lever:fake-404"], remove: [] },
  });
  expect(((await refused.json()) as { added: string[] }).added).toEqual([]);
  expect(await boardsOf(pageB)).toEqual([]);
  // N's list is still N's
  const n = await pageN.request.get("/api/opportunities/starter");
  const added = (
    (await n.json()) as { entries: Array<{ added: boolean }> }
  ).entries.filter((e) => e.added);
  expect(added).toHaveLength(2);
});
