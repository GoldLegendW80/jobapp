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

const shots = makeShots("profile-about", "about");

/**
 * The one page profile and its About you document (SPEC-profile, "About you") against the real
 * stack through Caddy: a fresh account gets a made up profile through the operator CLI, finds it
 * written once as a Markdown document, edits the Experience part in place (saved as typed), sees
 * without a key what is missing, saves a made up API key, clicks Done (the worker replays the
 * fake stream `profile_interpret`: no model is called), reads what the app understood (one job
 * added, one updated, one the text does not write refused), applies it in one click (the jobs
 * keep their keys) and undoes it.
 *
 * Locally, on a scratch Compose project with the fake agent backend (as profile-titles):
 *   E2E_CADDY_URL=http://127.0.0.1:8095 E2E_COMPOSE_PROJECT=jobapp-p5 \
 *     pnpm --dir web exec playwright test profile-about.spec.ts
 * On the Jetson it runs inside web/e2e/identity-all.config.ts.
 */
test.describe.configure({ mode: "serial", timeout: 120_000 });
test.skip(!caddyUrl, "set E2E_CADDY_URL with the Compose stack running");

const run = Date.now().toString(36);
const EMAIL = `e2e-about-${run}@example.org`;
const NAME = "Robin Example";
const PASSWORD = `about journey ${run} passphrase`;
const tail = () => randomBytes(30).toString("base64url").replace(/[-_]/g, "x");
const KEY = `sk-ant-api03-e2eabout${tail()}${tail()}Ab0u`;
const CSRF = { "X-Requested-With": "jobapp" };
// What the person types in the Experience part (the fake stream reads this text).
const EDITED = [
  "### Senior data engineer, Example Robotics",
  "March 2021 to now · Lyon · CDI",
  "",
  "### Analyst, Example Data",
  "2018 to 2021 · Paris",
  "",
  "### Intern, Example Labs",
  "2017 to 2018 · Grenoble · internship",
].join("\n");

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
        city: "Lyon",
        country: "France",
        linkedin_url: null,
        website: null,
        links: [],
        languages: [{ name: "French", level: "Native" }],
      },
      experience: {
        employers: [
          {
            key: "example-robotics",
            organisation: "Example Robotics",
            title: "Data engineer",
            start: "2021-03",
            end: null,
            location: "Lyon",
            contract: "CDI",
          },
          {
            key: "example-data",
            organisation: "Example Data",
            title: "Analyst",
            start: "2018",
            end: "2021",
            location: "Paris",
            contract: null,
          },
        ],
        education: [],
      },
      facts: { blocks: [] },
      skills: {
        years_experience: 5,
        have: [{ name: "data", ...terms(["SQL", "dbt", "Python"]) }],
        adjacent: [],
        none: [],
        gates: [],
      },
      search: {
        title_include: terms(["Data engineer"]),
        title_exclude: terms([]),
        location: terms(["Lyon"]),
        contract_types: ["CDI"],
        remote_stance: "hybrid",
        min_fit: 70,
        days: 30,
        never_apply: [],
      },
      answers: {
        work_authorization: [],
        notice_period: "Three months.",
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

const experience = () => page.getByRole("region", { name: "Experience" });

async function employers() {
  const response = await page.request.get("/api/profile");
  expect(response.status()).toBe(200);
  const body = (await response.json()) as {
    sections: { experience: { employers: { key: string; title: string }[] } };
  };
  return body.sections.experience.employers.map((e) => [e.key, e.title]);
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

test("the profile is one page and About you is written once from it", async () => {
  await page.goto(cliLink(EMAIL));
  await setPassword(page, PASSWORD, NAME);
  expect(cliImportProfile(EMAIL, fakeExport())).toMatch(/revision/i);
  await page.goto("/profile");
  await expect(page.getByRole("tab")).toHaveCount(0);
  await expect(page.getByRole("list", { name: "Ready for" })).toContainText(
    "Scoring postings",
  );
  await expect(
    page
      .getByRole("navigation", { name: "Parts of About you" })
      .getByRole("link"),
  ).toHaveText(["Experience", "Education", "Skills", "Answers"]);
  await expect(experience()).toContainText("Data engineer, Example Robotics");
  await expect(experience()).toContainText("March 2021 to now · Lyon · CDI");
  await shots(page, "one-page");
});

test("without a key the text saves and the line under Done says what is missing", async () => {
  await experience().getByRole("button", { name: "Edit Experience" }).click();
  await expect(
    experience().getByText(
      "The app needs a model key to update your profile from this text.",
    ),
  ).toBeVisible();
  await expect(
    experience().getByRole("link", { name: "Add one on the Account page" }),
  ).toHaveAttribute("href", "/account");
  await experience().getByLabel("Text").fill(EDITED);
  await expect(
    experience().getByRole("region", { name: "Preview" }),
  ).toContainText("Intern, Example Labs");
  await expect(experience().getByText("Saved")).toBeVisible();
  await experience().getByRole("button", { name: "Done" }).click();
  await expect(experience().getByLabel("Text")).toHaveCount(0);
  await expect(experience()).toContainText(
    "Your profile does not have this text yet.",
  );
  // The text saved; the profile did not change.
  expect(await employers()).toEqual([
    ["example-robotics", "Data engineer"],
    ["example-data", "Analyst"],
  ]);
  await shots(page, "no-key");
});

test("with a key: Done shows what the app understood, Apply writes it, Undo puts it back", async () => {
  const saved = await page.request.put("/api/me/model-credential", {
    headers: CSRF,
    data: { kind: "anthropic_api_key", secret: KEY },
  });
  expect(saved.status()).toBe(200);
  await page.reload();
  await expect(experience()).toContainText(/at list prices\./);
  await experience().getByRole("button", { name: "Update my profile" }).click();
  const understood = experience().getByRole("region", {
    name: "What the app understood",
  });
  await expect(understood).toBeVisible({ timeout: 90_000 });
  await expect(understood).toContainText(
    "1 job added: Intern at Example Labs, 2017 to 2018",
  );
  await expect(understood).toContainText(
    "1 job updated: Senior data engineer at Example Robotics, March 2021 to now",
  );
  // A job the text does not write is not taken.
  await expect(understood).toContainText("A job at Example Space");
  await expect(understood).not.toContainText(/[–—]| - /);
  // Locked facts change only on Apply.
  expect(await employers()).toHaveLength(2);
  await shots(page, "understood");

  await understood.getByRole("button", { name: "Apply" }).click();
  await expect(
    experience().getByText("Applied to your profile:"),
  ).toBeVisible();
  expect(await employers()).toEqual([
    ["example-robotics", "Senior data engineer"],
    ["example-data", "Analyst"],
    ["example-labs", "Intern"],
  ]);
  await experience().getByRole("button", { name: "Undo" }).click();
  await expect(experience().getByText("Applied to your profile:")).toHaveCount(
    0,
  );
  expect(await employers()).toEqual([
    ["example-robotics", "Data engineer"],
    ["example-data", "Analyst"],
  ]);
  expect(errors).toEqual([]);
});
