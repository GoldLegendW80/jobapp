import { expect, test, type BrowserContext, type Page } from "@playwright/test";

import {
  caddyUrl,
  clearAttempts,
  cliLink,
  makeShots,
  newContext,
  setPassword,
  watchErrors,
} from "./stack";

const takeShots = makeShots("apply-a3", "answers");

async function shots(page: Page, name: string, fullPage = true) {
  await expect(page.locator("[data-sonner-toast]")).toHaveCount(0, {
    timeout: 15_000,
  });
  await takeShots(page, name, fullPage);
}

/**
 * Apply A3 against the real stack through Caddy, no mocks, one fresh account and only made up
 * data: person A's answers to the fields A3 added (what to call them, years and degree, yes or no
 * for the work setup and the consents, the salary stance with a range, the equal opportunity
 * policy with a stored answer) are saved through the api, as Fill my profile or a reading of
 * About you would save them, then the profile page writes each of them, in words, in the Answers
 * part of About you (a made up API key is saved: without one the answers are fields, owner
 * 2026-11-07), and a reload reads the same.
 *
 * Locally, on a scratch Compose project (its own volumes, torn down afterwards):
 *   HTTP_PORT=8096 docker compose -p jobapp-a3 -f compose.yml -f compose.mac.yml up -d --wait
 *   docker compose -p jobapp-a3 -f compose.yml exec -T api alembic upgrade head
 *   E2E_CADDY_URL=http://127.0.0.1:8096 E2E_COMPOSE_PROJECT=jobapp-a3 \
 *     pnpm --dir web exec playwright test apply-a3.spec.ts
 *   docker compose -p jobapp-a3 -f compose.yml down -v
 */
test.describe.configure({ mode: "serial", timeout: 120_000 });
test.skip(!caddyUrl, "set E2E_CADDY_URL with the Compose stack running");

const run = Date.now().toString(36);
const EMAIL = `e2e-apply-a3-${run}@example.org`;
const NAME = "Alex Example";
const PASSWORD = `apply a3 journey ${run} passphrase`;
const CSRF = { "X-Requested-With": "jobapp" };
// Only its format is checked when it is saved; no model is called by this journey.
const KEY = `sk-ant-api03-e2egood${"Aa3x".repeat(20)}Rn42`;

const A3_ANSWERS = {
  preferred_name: "Alex",
  pronouns: "they/them",
  years_experience_stated: "About 3.5 years",
  highest_degree: "Master's degree",
  highest_degree_year: 2022,
  willing_onsite: true,
  willing_hybrid: true,
  previously_applied_default: false,
  background_check_consent: false,
  reference_check_consent: true,
  salary_stance: "range",
  salary_ranges: [{ currency: "EUR", minimum: 50000, maximum: 60000 }],
  eeo_policy: "stored",
  eeo_answers: [{ topic: "gender", answer: "Prefer not to say" }],
};

/** What the Answers part says of each value (api/app/profile/about_text.py, the answers part). */
const WRITTEN = [
  "Preferred name",
  "Alex",
  "Pronouns",
  "they/them",
  "Years of experience, as I say it",
  "About 3.5 years",
  "Highest degree",
  "Master's degree",
  "Year: 2022",
  "Yes, I can work on site.",
  "Yes, I can work hybrid.",
  "No, by default I say I have not applied before.",
  "No, I do not consent to background checks.",
  "Yes, I consent to reference checks.",
  "On forms, I give the salary range below.",
  "EUR 50000 to 60000 a year",
  "I answer the equal opportunity questions with the answers below.",
  "Gender: Prefer not to say",
];

let context: BrowserContext;
let page: Page;
let errors: string[];

test.beforeAll(async ({ browser }) => {
  context = await newContext(browser);
  page = await context.newPage();
  errors = watchErrors(page);
});

test.afterAll(async () => {
  await context.close();
  clearAttempts([EMAIL]);
});

const answersPart = () =>
  page.getByRole("region", { name: "Answers", exact: true });

test("person A's new answers, saved through the api, are written in About you", async () => {
  await page.goto(cliLink(EMAIL));
  await setPassword(page, PASSWORD, NAME);
  // The document is written from the profile the first time it is read: save the answers first.
  const profile = (await (await page.request.get("/api/profile")).json()) as {
    revision: number;
  };
  const saved = await page.request.put("/api/profile/answers", {
    headers: CSRF,
    data: { base_revision: profile.revision, data: A3_ANSWERS },
  });
  expect(saved.status(), await saved.text()).toBe(200);
  const key = await page.request.put("/api/me/model-credential", {
    headers: CSRF,
    data: { kind: "anthropic_api_key", secret: KEY },
  });
  expect(key.status(), await key.text()).toBe(200);

  await page.goto("/profile/about#about-answers");
  const answers = answersPart();
  for (const line of WRITTEN) await expect(answers).toContainText(line);
  await expect(answers).not.toContainText(/[–—]| - /);
  await shots(page, "answers-written");
  expect(errors).toEqual([]);
});

test("a reload shows every value", async () => {
  await page.goto("/profile/about");
  const answers = answersPart();
  for (const line of WRITTEN) await expect(answers).toContainText(line);
  const stored = (await (await page.request.get("/api/profile")).json()) as {
    sections: { answers: Record<string, unknown> };
  };
  expect(stored.sections.answers).toMatchObject(A3_ANSWERS);
  await shots(page, "answers-reloaded");
  expect(errors).toEqual([]);
});
