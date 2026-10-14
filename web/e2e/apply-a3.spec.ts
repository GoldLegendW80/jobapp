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
 * data: person A fills the new fields of the Answers tab (what to call them, years and degree,
 * yes or no for the work setup and the consents, the salary stance with a range, the equal
 * opportunity policy with a stored answer), saves, reloads and sees every value. Then, at
 * 375 px and from the keyboard, a currency that is not three capitals is refused before any
 * request.
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

const field = (name: string) => page.getByLabel(name, { exact: true });

async function openAnswers() {
  const tab = page.getByRole("tab", { name: /^Answers, \d+% complete$/ });
  await tab.click();
  await expect(tab).toHaveAttribute("aria-selected", "true");
  await expect(page.getByRole("form", { name: "Answers" })).toBeVisible();
}

test("person A fills the new Answers fields and saves", async () => {
  await page.goto(cliLink(EMAIL));
  await setPassword(page, PASSWORD, NAME);
  await page.goto("/profile");
  await openAnswers();

  await field("Preferred name").fill("Alex");
  await field("Pronouns").fill("they/them");
  await field("Years of experience, as you state them").fill("About 3.5 years");
  await field("Highest degree").fill("Master's degree");
  await field("Year of the highest degree").fill("2022");

  await field("Willing to work on site").selectOption("yes");
  await field("Willing to work hybrid").selectOption("yes");
  await field("Previously applied or worked at a company").selectOption("no");
  await field("Consent to a background check").selectOption("no");
  await field("Consent to a reference check").selectOption("yes");

  await field("Salary stance").selectOption("range");
  await page.getByRole("button", { name: "Add range" }).click();
  await expect(field("Currency of range 1")).toBeFocused();
  await page.keyboard.type("EUR");
  await field("Minimum of range 1").fill("50000");
  await field("Maximum of range 1").fill("60000");

  await field("What to do").selectOption("stored");
  await page.getByRole("button", { name: "Add answer" }).click();
  await field("Answer for topic 1").fill("Prefer not to say");

  await shots(page, "answers-filled");
  await page.getByRole("button", { name: "Save answers" }).click();
  await expect(page.getByText("Answers saved.").first()).toBeVisible();
  expect(errors).toEqual([]);
});

test("a reload shows every value", async () => {
  await page.goto("/profile");
  await openAnswers();
  await expect(field("Preferred name")).toHaveValue("Alex");
  await expect(field("Pronouns")).toHaveValue("they/them");
  await expect(field("Years of experience, as you state them")).toHaveValue(
    "About 3.5 years",
  );
  await expect(field("Highest degree")).toHaveValue("Master's degree");
  await expect(field("Year of the highest degree")).toHaveValue("2022");
  await expect(field("Willing to work on site")).toHaveValue("yes");
  await expect(field("Willing to work hybrid")).toHaveValue("yes");
  await expect(field("Previously applied or worked at a company")).toHaveValue(
    "no",
  );
  await expect(field("Consent to a background check")).toHaveValue("no");
  await expect(field("Consent to a reference check")).toHaveValue("yes");
  await expect(field("Salary stance")).toHaveValue("range");
  await expect(field("Currency of range 1")).toHaveValue("EUR");
  await expect(field("Minimum of range 1")).toHaveValue("50000");
  await expect(field("Maximum of range 1")).toHaveValue("60000");
  await expect(field("What to do")).toHaveValue("stored");
  await expect(field("Topic 1")).toHaveValue("gender");
  await expect(field("Answer for topic 1")).toHaveValue("Prefer not to say");
  await shots(page, "answers-reloaded");
  expect(errors).toEqual([]);
});

test("at 375 px, from the keyboard: a bad currency is refused before any request", async () => {
  await page.setViewportSize({ width: 375, height: 812 });
  await field("Currency of range 1").fill("eur");
  await page.getByRole("button", { name: "Save answers" }).click();
  await expect(
    page.getByText("Enter a currency code of three capital letters"),
  ).toBeVisible();
  await expect(field("Currency of range 1")).toHaveAttribute(
    "aria-invalid",
    "true",
  );
  await field("Currency of range 1").fill("EUR");
  await page.getByRole("button", { name: "Save answers" }).click();
  await expect(page.getByText("Answers saved.").first()).toBeVisible();
  expect(errors).toEqual([]);
});
