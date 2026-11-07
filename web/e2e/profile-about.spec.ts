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
 * stack through Caddy: a fresh account gets a made up profile through the operator CLI and, with
 * no model key, finds its skills and answers as fields and its facts as text (the jobs stay a
 * form, the search is in Settings, Search). It saves a fact part to the profile at Done, changes
 * an answer by hand (owner, 2026-11-07), saves a made up API key, edits the Skills part in place,
 * clicks Done (the worker replays the fake stream `profile_interpret`: no model is called), reads
 * what the app understood (the years and two skills changed, one term the text does not write
 * refused), applies it in one click and undoes it.
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
// What the person types in the Skills part (the fake stream reads this text).
const EDITED = [
  "Years of experience: 5",
  "",
  "### What I have",
  "- data: SQL, dbt, Airflow",
  "",
  "### What I do not have",
  "- Java",
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
        ],
        education: [],
      },
      facts: { blocks: [] },
      skills: {
        years_experience: 4,
        have: [{ name: "data", ...terms(["SQL", "dbt"]) }],
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

const skills = () => page.getByRole("region", { name: "Skills" });

async function profile() {
  const response = await page.request.get("/api/profile");
  expect(response.status()).toBe(200);
  return (await response.json()) as {
    sections: {
      skills: { years_experience: number; have: { terms: string[] }[] };
      facts: { blocks: { title: string; body: string }[] };
      answers: { notice_period: string | null };
    };
  };
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

test("the profile is one page; without a key About you holds the skills and answers as fields", async () => {
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
  ).toHaveText(["Skills", "Answers"]);
  const fields = page.getByRole("form", { name: "Skills" });
  await expect(
    fields.getByLabel("Years of experience", { exact: true }),
  ).toHaveValue("4");
  await expect(
    fields.getByRole("button", { name: "What I have, data" }),
  ).toBeVisible();
  const answers = page.getByRole("form", { name: "Answers" });
  await answers
    .getByRole("button", { name: /^Availability and conditions,/ })
    .click();
  await expect(
    answers.getByLabel("Notice period", { exact: true }),
  ).toHaveValue("Three months.");
  // The jobs stay a form; the search is in Settings, Search.
  await expect(page.getByRole("form", { name: "Experience" })).toBeVisible();
  await expect(page.getByRole("form", { name: "Search" })).toHaveCount(0);
  await shots(page, "one-page");
});

test("a fact part is saved to the profile at Done, without a key", async () => {
  await page.getByRole("button", { name: "Add a part" }).click();
  const added = page.getByRole("region", { name: "New part" });
  await added.getByLabel("Heading").fill("Projects");
  const projects = page.getByRole("region", { name: "Projects" });
  await projects.getByLabel("Text").fill("- Built the streaming platform");
  await projects.getByRole("button", { name: "Done" }).click();
  await expect(projects.getByText("Applied to your profile:")).toBeVisible();
  expect((await profile()).sections.facts.blocks).toEqual([
    { title: "Projects", body: "- Built the streaming platform" },
  ]);
});

test("without a key an answer changes by hand and the Answers part is written again", async () => {
  await expect(
    skills().getByRole("button", { name: "Edit Skills" }),
  ).toHaveCount(0);
  await expect(
    skills().getByRole("link", { name: "Add one on the Account page" }),
  ).toHaveAttribute("href", "/account");
  const answers = page.getByRole("form", { name: "Answers" });
  await answers
    .getByLabel("Notice period", { exact: true })
    .fill("Two months.");
  await answers.getByRole("button", { name: "Save answers" }).click();
  await expect(page.getByText("Answers saved.").first()).toBeVisible();
  await expect
    .poll(async () => (await profile()).sections.answers.notice_period)
    .toBe("Two months.");
  // The skills did not change.
  expect((await profile()).sections.skills.years_experience).toBe(4);
  await shots(page, "no-key");
});

test("with a key: what the app understood, Apply writes it, Undo puts it back", async () => {
  const saved = await page.request.put("/api/me/model-credential", {
    headers: CSRF,
    data: { kind: "anthropic_api_key", secret: KEY },
  });
  expect(saved.status()).toBe(200);
  await page.reload();
  // With a key the skills are text again: the part is edited in place and Done reads it.
  await skills().getByRole("button", { name: "Edit Skills" }).click();
  await expect(skills()).toContainText(/at list prices\./);
  await skills().getByLabel("Text").fill(EDITED);
  await expect(skills().getByRole("region", { name: "Preview" })).toContainText(
    "Java",
  );
  await skills().getByRole("button", { name: "Done" }).click();
  const understood = skills().getByRole("region", {
    name: "What the app understood",
  });
  await expect(understood).toBeVisible({ timeout: 90_000 });
  await expect(understood).toContainText("Years of experience: 5");
  await expect(understood).toContainText(
    "1 skill added to what I have: Airflow",
  );
  await expect(understood).toContainText(
    "1 skill added to what I do not have: Java",
  );
  // A term the text does not write is not taken.
  await expect(understood).toContainText(
    "1 term of data: not written in this part.",
  );
  await expect(understood).not.toContainText(/[–—]| - /);
  expect((await profile()).sections.skills.years_experience).toBe(4);
  await shots(page, "understood");

  await understood.getByRole("button", { name: "Apply" }).click();
  await expect(skills().getByText("Applied to your profile:")).toBeVisible();
  const applied = (await profile()).sections.skills;
  expect(applied.years_experience).toBe(5);
  expect(applied.have[0]?.terms).toEqual(["SQL", "dbt", "Airflow"]);
  await skills().getByRole("button", { name: "Undo" }).click();
  await expect(skills().getByText("Applied to your profile:")).toHaveCount(0);
  expect((await profile()).sections.skills.years_experience).toBe(4);
  expect(errors).toEqual([]);
});
