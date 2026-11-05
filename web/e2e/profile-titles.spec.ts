import { randomBytes } from "node:crypto";

import { expect, test, type BrowserContext, type Page } from "@playwright/test";

import {
  caddyUrl,
  clearAttempts,
  cliImportProfile,
  cliLink,
  makeShots,
  newContext,
  setPassword,
  watchErrors,
} from "./stack";

const shots = makeShots("profile-titles", "titles");

/**
 * Job title ideas and the place of the profile (SPEC-profile, "Job title suggestions") against
 * the real stack through Caddy: a fresh account gets a made up profile through the operator CLI,
 * takes the place of its profile in one click, saves a made up API key, asks for ideas (the
 * worker replays the fake stream `profile_titles`: no model is called), adds one in one click and
 * saves. The ideas the code refuses (a cybersecurity title, a dash, a repeat) never show.
 *
 * Locally, on a scratch Compose project with the fake agent backend (as agents-triage):
 *   E2E_CADDY_URL=http://127.0.0.1:8095 E2E_COMPOSE_PROJECT=jobapp-p5 \
 *     pnpm --dir web exec playwright test profile-titles.spec.ts
 * On the Jetson it runs inside web/e2e/identity-all.config.ts.
 */
test.describe.configure({ mode: "serial", timeout: 120_000 });
test.skip(!caddyUrl, "set E2E_CADDY_URL with the Compose stack running");

const run = Date.now().toString(36);
const EMAIL = `e2e-titles-${run}@example.org`;
const NAME = "Sam Example";
const PASSWORD = `titles journey ${run} passphrase`;
const tail = () => randomBytes(30).toString("base64url").replace(/[-_]/g, "x");
const KEY = `sk-ant-api03-e2etitles${tail()}${tail()}Ti7l`;
const CSRF = { "X-Requested-With": "jobapp" };

function fakeExport() {
  const terms = (list: string[]) => ({ advanced_regex: false, terms: list });
  return {
    format: "jobapp-profile",
    version: 1,
    exported_at: "2026-11-05T08:00:00Z",
    sections: {
      identity: {
        full_name: NAME,
        headline: "Data Engineer",
        email: EMAIL,
        phone: null,
        city: "Lille",
        country: "France",
        linkedin_url: null,
        website: null,
        links: [],
        languages: [{ name: "French", level: "Native" }],
      },
      experience: {
        employers: [
          {
            key: "acme",
            organisation: "Acme Robotics",
            title: "Data Engineer",
            start: "2022-03",
            end: null,
            location: "Lille",
            contract: "CDI",
          },
        ],
        education: [],
      },
      facts: { blocks: [] },
      skills: {
        years_experience: 4,
        have: [{ name: "data", ...terms(["sql", "dbt", "python"]) }],
        adjacent: [],
        none: [],
        gates: [],
      },
      search: {
        title_include: terms(["ai engineer"]),
        title_exclude: terms(["intern"]),
        location: terms(["Remote"]),
        contract_types: ["CDI"],
        remote_stance: "hybrid",
        min_fit: 70,
        days: 30,
        never_apply: [],
      },
      answers: {
        work_authorization: [],
        notice_period: null,
        earliest_start: null,
        salary_policy: null,
        relocation: null,
        travel: null,
        motivation: null,
        heard_about: null,
        leave_blank: [],
      },
    },
  };
}

let context: BrowserContext;
let page: Page;
let errors: string[];

const ideas = () => page.getByRole("region", { name: "Suggested for you" });
const terms = (list: string) =>
  page.getByRole("list", { name: `Terms of ${list}` });

async function openSearch() {
  await page.goto("/profile?tab=search");
  await expect(page.getByRole("form", { name: "Search" })).toBeVisible();
}

async function saveSearch() {
  await page.getByRole("button", { name: "Save search" }).click();
  await expect(page.getByText("Search saved.").first()).toBeVisible();
}

test.beforeAll(async ({ browser }) => {
  context = await newContext(browser);
  page = await context.newPage();
  errors = watchErrors(page);
});

test.afterAll(async () => {
  await context.close();
  clearAttempts([EMAIL]);
});

test("without a key the ideas say what is missing; the place of the profile is one click", async () => {
  await page.goto(cliLink(EMAIL));
  await setPassword(page, PASSWORD, NAME);
  expect(cliImportProfile(EMAIL, fakeExport())).toMatch(/revision/i);
  await openSearch();
  await expect(ideas()).toContainText(
    "Job title ideas from your profile need a model key.",
  );
  await expect(
    ideas().getByRole("link", { name: "Add a model key" }),
  ).toHaveAttribute("href", "/account");
  await page
    .getByRole("button", { name: "Add Lille, from your profile, to Places" })
    .click();
  await expect(terms("Places")).toContainText("Lille");
  await saveSearch();
  await expect(
    page.getByRole("button", { name: /Add Lille, from your profile/ }),
  ).toHaveCount(0);
  await shots(page, "no-key");
});

test("with a key: the cost before the click, then ideas that passed the checks", async () => {
  const saved = await page.request.put("/api/me/model-credential", {
    headers: CSRF,
    data: { kind: "anthropic_api_key", secret: KEY },
  });
  expect(saved.status()).toBe(200);
  await openSearch();
  await expect(ideas()).toContainText(/each time, at list prices\./);
  await ideas().getByRole("button", { name: "Suggest titles" }).click();
  const add = page.getByRole("button", {
    name: "Add Analytics engineer to Job titles",
  });
  await expect(add).toBeVisible({ timeout: 90_000 });
  await expect(add).toHaveAccessibleDescription(
    "Your SQL and dbt work matches this role.",
  );
  // What the code refused never shows: a cybersecurity title, a dash, a repeat.
  await expect(ideas()).not.toContainText(/security/i);
  await expect(ideas()).not.toContainText(/[–—]| - /);
  await expect(ideas().getByRole("button", { name: /^Add / })).toHaveCount(5);
  await shots(page, "ideas");
});

test("one click adds an idea to the list; Save keeps it and the idea leaves", async () => {
  await page
    .getByRole("button", { name: "Add Analytics engineer to Job titles" })
    .click();
  await expect(terms("Job titles")).toContainText("Analytics engineer");
  await saveSearch();
  await page.reload();
  await expect(terms("Job titles")).toContainText("Analytics engineer");
  await expect(
    page.getByRole("button", { name: "Add Analytics engineer to Job titles" }),
  ).toHaveCount(0);
  await expect(
    page.getByRole("button", {
      name: "Add Data platform engineer to Job titles",
    }),
  ).toBeVisible();
  expect(errors).toEqual([]);
});
