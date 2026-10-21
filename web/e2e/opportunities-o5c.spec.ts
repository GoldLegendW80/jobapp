import { expect, test, type BrowserContext, type Page } from "@playwright/test";

import {
  CSRF,
  caddyUrl,
  invented,
  signUp,
  type Person,
} from "./documents-helpers";
import { clearAttempts, makeShots, newContext, watchErrors } from "./stack";

/**
 * Opportunities O5c against the real stack through Caddy: the wizard's starter list of boards and
 * sites in Search options (/profile?tab=search), for an account that skipped the wizard. The
 * stack sends every ATS call to the fake ATS server (tests/ats-stub.compose.yml), so the api
 * proposes from its stub catalog (api/app/opportunities/starter_stub.json): invented companies on
 * the fake server's orgs, never a real board.
 *
 * An invented admin invites a member, S, who never opens the wizard: S's roles (AI engineer, in
 * Paris) are saved, then S opens Search options at phone width. With no board and no site yet,
 * the list is open at once, nothing ticked. While the search has unsaved changes the list waits.
 * S removes Google, ticks one board and adds it: the board appears under Company boards at once,
 * and a save of the search right after keeps it (no conflict). After a reload the list is closed
 * (the search has a board now), shows the board as added and never Google again; "Add all" adds
 * the rest. No screen shows a dash.
 *
 *   E2E_CADDY_URL=... E2E_ATS_FORMS_URL=... E2E_COMPOSE_PROJECT=... \
 *     pnpm --dir web exec playwright test -c e2e/opportunities-all.config.ts opportunities-o5c.spec.ts
 */
test.describe.configure({ mode: "serial" });
test.skip(!caddyUrl, "set E2E_CADDY_URL with the Compose stack running");

const DASH = /[‒–—―]| - /;
const PASSWORD = "Starter-search-2026!";
const HELD =
  "Save or undo your changes to the search first: the list adds to your saved search.";
const shotsAt = makeShots("opportunities-o5c", "o5c");
async function shots(page: Page, name: string) {
  await shotsAt(page, name, false);
  await page.setViewportSize({ width: 375, height: 740 });
  await page.emulateMedia({ colorScheme: "light" });
}

let admin: Person;
let adminContext: BrowserContext;
let adminPage: Page;
const run = `${Date.now().toString(36)}${Math.random().toString(36).slice(2, 6)}`;
const S = {
  email: `e2e-o5c-skip-${run}@example.org`,
  name: "Sam Exampleton",
};
let contextS: BrowserContext;
let pageS: Page;
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

async function searchOf(page: Page) {
  const read = await page.request.get("/api/profile");
  const body = (await read.json()) as {
    sections: {
      search: {
        days: number;
        boards: Array<{ org: string; enabled: boolean }>;
        browser_sources?: Array<{ kind: string }>;
      };
    };
  };
  return body.sections.search;
}

const card = (page: Page) =>
  page.locator("section").filter({
    has: page.getByRole("heading", { name: "Suggested boards and sites" }),
  });
const list = (page: Page) =>
  page.getByRole("region", { name: "Boards and sites to search" });
const searchForm = (page: Page) => page.getByRole("form", { name: "Search" });
const days = (page: Page) =>
  searchForm(page).getByLabel("Posting age window (days)");

async function searchOptions(page: Page) {
  await page.goto("/profile?tab=search");
  await expect(searchForm(page)).toBeVisible();
}

test.beforeAll(async ({ browser }) => {
  admin = invented("o5c");
  adminContext = await newContext(browser);
  adminPage = await adminContext.newPage();
  contextS = await newContext(browser);
  pageS = await contextS.newPage();
  await pageS.setViewportSize({ width: 375, height: 740 });
  errors = watchErrors(pageS);
});

test.afterAll(async () => {
  await Promise.all([adminContext.close(), contextS.close()]);
  clearAttempts([admin.email, S.email]);
});

test("S skips the wizard: Search options open the list at once, nothing ticked", async () => {
  await signUp(adminPage, admin);
  const made = await adminPage.request.post("/api/admin/invites", {
    headers: CSRF,
    data: { email: S.email },
  });
  expect(made.status()).toBe(201);
  await pageS.goto(((await made.json()) as { path: string }).path);
  await pageS.getByLabel("Name").fill(S.name);
  await pageS.getByLabel(/^(New password|Password)$/).fill(PASSWORD);
  await pageS.getByLabel("Confirm password").fill(PASSWORD);
  await pageS.getByLabel("Confirm password").press("Enter");
  await expect(pageS.locator("html[data-onboarding]")).toHaveCount(1);
  await putSection(pageS, "search", (stored) => ({
    ...stored,
    title_include: terms("AI engineer"),
    location: terms("Paris"),
    contract_types: ["CDI"],
  }));

  await searchOptions(pageS);
  await expect(
    card(pageS).getByRole("button", { name: "Hide the list" }),
  ).toHaveAttribute("aria-expanded", "true");
  for (const company of [
    "Example Robotics",
    "Sample Analytics",
    "Google",
    "Business France VIE",
  ])
    await expect(list(pageS).getByText(company, { exact: true })).toBeVisible();
  for (const box of await list(pageS).getByRole("checkbox").all())
    await expect(box).not.toBeChecked();
  await expect(list(pageS)).toContainText(
    "You can add any other company board by its address under Company boards.",
  );
  expect((await searchOf(pageS)).boards).toEqual([]);
  await noDash(pageS);
  await shots(pageS, "search-starter-list");
});

test("the list waits while the search has unsaved changes", async () => {
  await days(pageS).fill("21");
  await expect(list(pageS).getByText(HELD)).toBeVisible();
  await expect(
    list(pageS).getByRole("button", { name: "Add all" }),
  ).toBeDisabled();
  await expect(
    list(pageS).getByRole("button", { name: "Remove Google from the list" }),
  ).toBeDisabled();
  await noDash(pageS);
  await shots(pageS, "search-starter-held");
  await days(pageS).fill("30");
  await expect(list(pageS).getByText(HELD)).toHaveCount(0);
  await expect(
    list(pageS).getByRole("button", { name: "Add all" }),
  ).toBeEnabled();
});

test("S removes Google, adds one board, and a save right after keeps it", async () => {
  await list(pageS)
    .getByRole("button", { name: "Remove Google from the list" })
    .click();
  await expect(list(pageS).getByText("Taken off the list.")).toBeVisible();
  await expect(list(pageS).getByText("Google", { exact: true })).toHaveCount(0);
  await list(pageS)
    .getByRole("checkbox", { name: /Example Robotics/ })
    .check();
  await list(pageS)
    .getByRole("button", { name: "Add the ticked ones" })
    .click();
  await expect(
    list(pageS).getByText(
      "Added 1 board or site. They are now under Company boards and Job sites in your browser.",
    ),
  ).toBeVisible();
  await expect(searchForm(pageS).getByLabel("Org of board 1")).toHaveValue(
    "fake-ok",
  );

  await days(pageS).fill("14");
  await searchForm(pageS).getByRole("button", { name: "Save search" }).click();
  await expect(pageS.getByText("Search saved.")).toBeVisible();
  const search = await searchOf(pageS);
  expect(search.days).toBe(14);
  expect(search.boards.map((b) => `${b.org}:${b.enabled}`)).toEqual([
    "fake-ok:true",
  ]);
  await noDash(pageS);
  await shots(pageS, "search-starter-added");
});

test("after a reload the list is closed, the board added, Google never back; Add all adds the rest", async () => {
  await searchOptions(pageS);
  const toggle = card(pageS).getByRole("button", { name: "Show the list" });
  await expect(toggle).toHaveAttribute("aria-expanded", "false");
  await toggle.click();
  await expect(list(pageS).getByText("Added", { exact: true })).toHaveCount(1);
  await expect(list(pageS).getByText("Google", { exact: true })).toHaveCount(0);
  await list(pageS).getByRole("button", { name: "Add all" }).click();
  await expect(
    list(pageS).getByText(/^Added \d+ boards and sites\./),
  ).toBeVisible();
  await expect(
    list(pageS).getByRole("button", { name: "Add all" }),
  ).toBeDisabled();
  const search = await searchOf(pageS);
  expect(search.boards.map((b) => b.org)).toContain("fake-ok2");
  expect(search.browser_sources?.map((s) => s.kind)).toEqual(["vie"]);
  expect(search.boards.every((b) => b.enabled)).toBe(true);
  expect(errors).toEqual([]);
});
