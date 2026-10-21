import fs from "node:fs";
import path from "node:path";

import { expect, test, type BrowserContext, type Page } from "@playwright/test";

import { caddyUrl, invented, seedDocuments, signUp } from "./documents-helpers";
import { deckPosting } from "./opportunities-helpers";
import {
  clearAttempts,
  makeShots,
  newContext,
  root,
  watchErrors,
} from "./stack";

/** O10 uses only an invented account and posting. The existing opportunities group supplies
 * fake boards, feeds and starter data. This journey also records the 375px page inventory that
 * must be inspected before creating the other lanes' issues. It changes no other lane's page. */
test.describe.configure({ mode: "serial" });
test.skip(!caddyUrl, "set E2E_CADDY_URL with the Compose stack running");

const person = invented("o10");
const shots = makeShots("opportunities-o10", "o10");
let context: BrowserContext;
let page: Page;
let errors: string[];
let postingId: string;
let baseId: string;

test.beforeAll(async ({ browser }) => {
  clearAttempts([person.email]);
  context = await newContext(browser);
  page = await context.newPage();
  errors = watchErrors(page);
  await signUp(page, person);
  postingId = await deckPosting(context.request, person, {
    title: "Example data engineer",
    ats: null,
    fit: 95,
  });
  baseId = seedDocuments(person, postingId).base;
});

test.afterAll(async () => {
  await context.close();
  clearAttempts([person.email]);
});

test("the product pages fold details, retain settings and restore the open choice", async () => {
  await page.goto("/discover");
  await expect(page.getByTestId("deck-card")).toBeVisible();
  let advanced = page.getByRole("button", { name: "Advanced", exact: true });
  await expect(advanced).toHaveAttribute("aria-expanded", "false");
  await expect(page.getByRole("group", { name: "Contract types" })).toHaveCount(
    0,
  );
  await shots(page, "discover-collapsed");
  await advanced.click();
  await expect(page.getByRole("checkbox")).toHaveCount(6);
  await shots(page, "discover-open");
  await page.reload();
  await expect(advanced).toHaveAttribute("aria-expanded", "true");

  await page.goto("/welcome?step=looking");
  advanced = page.getByRole("button", { name: "Advanced", exact: true });
  await expect(page.getByLabel("Roles", { exact: true })).toBeVisible();
  await expect(page.getByRole("checkbox")).toHaveCount(0);
  await shots(page, "wizard-targets-collapsed");
  await advanced.click();
  await expect(page.getByRole("checkbox")).toHaveCount(13);
  await shots(page, "wizard-targets-open");
  await page.reload();
  await expect(advanced).toHaveAttribute("aria-expanded", "true");

  await page.goto("/welcome?step=work");
  await expect(page.getByRole("group")).toHaveCount(2);
  for (const card of await page.getByRole("group").all()) {
    expect(await card.getByRole("checkbox").count()).toBeLessThanOrEqual(5);
  }
  await expect(page.getByRole("checkbox")).toHaveCount(6);
  await shots(page, "wizard-contracts");

  await page.goto("/welcome?step=pay");
  advanced = page.getByRole("button", { name: "Advanced", exact: true });
  await expect(advanced).toHaveAttribute("aria-expanded", "false");
  await expect(page.getByRole("textbox")).toHaveCount(0);
  await shots(page, "wizard-pay-collapsed");
  await advanced.click();
  let salary = page.getByRole("group", { name: "Yearly salary" });
  await salary.getByLabel("From", { exact: true }).fill("90000");
  await salary.getByLabel("To", { exact: true }).fill("10000");
  const daily = page.getByRole("group", { name: "Daily rate (freelance)" });
  await daily.getByLabel("From", { exact: true }).fill("600");
  await daily.getByLabel("To", { exact: true }).fill("800");
  await shots(page, "wizard-pay-open");
  await advanced.click();
  await page.getByRole("button", { name: "Next", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText(
    "The first figure is the lower one.",
  );
  await expect(advanced).toHaveAttribute("aria-expanded", "true");
  salary = page.getByRole("group", { name: "Yearly salary" });
  await expect(salary.getByLabel("From", { exact: true })).toBeFocused();
  await salary.getByLabel("To", { exact: true }).fill("100000");
  await expect(daily.getByLabel("From", { exact: true })).toHaveValue("600");
  await page.getByRole("button", { name: "Next", exact: true }).click();
  await expect(page).toHaveURL(/step=apply/);
  const response = await context.request.get("/api/profile");
  expect(response.ok()).toBe(true);
  const profile = (await response.json()) as {
    sections: { search: { compensation: unknown } };
  };
  expect(profile.sections.search.compensation).toEqual({
    salary: { min: 90000, max: 100000, currency: "EUR" },
    daily_rate: { min: 600, max: 800, currency: "EUR" },
  });

  await page.goto("/profile?tab=search");
  const sources = page
    .locator("section")
    .filter({
      has: page.getByRole("heading", { name: "Freelance and project sources" }),
    })
    .last();
  await expect(sources.getByRole("switch")).toHaveCount(4);
  await expect(
    sources.getByRole("button", { name: "Add a mission by hand" }),
  ).toHaveCount(1);
  advanced = sources.getByRole("button", { name: "Advanced", exact: true });
  await expect(advanced).toHaveAttribute("aria-expanded", "false");
  await shots(page, "sources-collapsed", true, () =>
    sources.scrollIntoViewIfNeeded(),
  );
  await advanced.click();
  await expect(
    sources.getByText("Words for Remote OK", { exact: true }),
  ).toBeVisible();
  await shots(page, "sources-open", true, () =>
    sources.scrollIntoViewIfNeeded(),
  );
  await page.reload();
  await expect(advanced).toHaveAttribute("aria-expanded", "true");
  expect(errors).toEqual([]);
});

const AUDIT_PAGES = [
  ["jobs", "/jobs"],
  ...["identity", "skills", "experience", "answers", "facts", "search"].map(
    (tab) => [`profile-${tab}`, `/profile?tab=${tab}`],
  ),
  ["profile-cv", "/profile/cv"],
  ["applications-settings", "/settings/applications"],
  ["account", "/account"],
  ["document-templates", "/documents/templates"],
  ["ingestion-documents", "/profile/fill?step=documents"],
  ["ingestion-prompt", "/profile/fill?step=prompt"],
  ["ingestion-review", "/profile/fill?step=review"],
  ["runs", "/runs"],
  ["administration", "/admin/users"],
] as const;

test("records each other page at 375px for the owning lane's follow-up", async () => {
  await page.setViewportSize({ width: 375, height: 740 });
  await page.emulateMedia({ colorScheme: "light" });
  const evidence = path.join(
    root,
    "tasks/evidence",
    process.env.E2E_EVIDENCE ?? "opportunities-o10",
  );
  fs.mkdirSync(evidence, { recursive: true });
  const inventory = [];
  const pages = [
    ...AUDIT_PAGES,
    ["posting-documents", `/jobs/${postingId}`],
    ["document-editor", `/jobs/${postingId}/documents/cv`],
    ["base-cv-editor", `/profile/cv/${baseId}`],
  ];
  for (const [name, route] of pages) {
    await page.goto(route);
    await expect(
      page.locator("main").getByRole("heading").first(),
    ).toBeVisible();
    expect(
      await page.evaluate(
        () =>
          document.documentElement.scrollWidth -
          document.documentElement.clientWidth,
      ),
      name,
    ).toBe(0);
    const labels = await page.locator("main").ariaSnapshot();
    inventory.push({ page: name, route, labels });
    await page.screenshot({
      path: path.join(evidence, `audit-${name}-375-light.png`),
      fullPage: true,
      animations: "disabled",
    });
  }
  fs.writeFileSync(
    path.join(evidence, "audit-375.json"),
    JSON.stringify(inventory, null, 2) + "\n",
  );
  expect(errors).toEqual([]);
});
