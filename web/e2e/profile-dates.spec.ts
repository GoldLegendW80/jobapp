import fs from "node:fs";
import os from "node:os";
import path from "node:path";

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

const takeShots = makeShots("profile-p7", "dates");

async function shots(page: Page, name: string, fullPage = true) {
  await expect(page.locator("[data-sonner-toast]")).toHaveCount(0, {
    timeout: 15_000,
  });
  await takeShots(page, name, fullPage);
}

/**
 * Profile P7 against the real stack through Caddy, no mocks, one fresh account and only made up
 * data: a year alone is a valid date. The journey adds an employer with years only and a school
 * with a start year and no end, saves, reloads and reads the same four characters back, turns
 * one year into a month from the keyboard at 375 px, sees that one change in the History drawer,
 * meets the two refusals (a form that is neither, an end certainly before the start), exports
 * the profile, changes it, imports the export back and finds the year alone again.
 *
 * Locally, on a scratch Compose project (its own volumes, torn down afterwards):
 *   HTTP_PORT=8097 docker compose -p jobapp-p7 -f compose.yml -f compose.mac.yml up -d --build --wait
 *   docker compose -p jobapp-p7 -f compose.yml exec -T api alembic upgrade head
 *   E2E_CADDY_URL=http://127.0.0.1:8097 E2E_COMPOSE_PROJECT=jobapp-p7 \
 *     pnpm --dir web exec playwright test profile-dates.spec.ts --workers=1
 *   docker compose -p jobapp-p7 -f compose.yml down -v
 *
 * In CI and on the Jetson it runs inside web/e2e/identity-all.config.ts.
 */
test.describe.configure({ mode: "serial", timeout: 180_000 });
test.skip(!caddyUrl, "set E2E_CADDY_URL with the Compose stack running");

const run = Date.now().toString(36);
const EMAIL = `e2e-dates-${run}@example.org`;
const NAME = "Morgan Example";
const PASSWORD = `dates journey ${run} passphrase`;

const field = (page: Page, label: string) =>
  page.getByLabel(label, { exact: true });

const tab = (page: Page, name: string) =>
  page.getByRole("tab", { name: new RegExp(`^${name}, \\d+% complete$`) });

/** Click Save and wait for the answer of that save itself, not for a toast of an earlier one. */
async function save(page: Page) {
  const saved = page.waitForResponse(
    (r) =>
      r.request().method() === "PUT" &&
      new URL(r.url()).pathname === "/api/profile/experience",
  );
  await page.getByRole("button", { name: "Save experience" }).click();
  expect((await saved).status()).toBe(200);
  await expect(page.getByText("Experience saved.").first()).toBeVisible();
}

/** Replace what a field holds by typing, key by key, as a person does. */
async function retype(page: Page, label: string, keys: string) {
  await field(page, label).focus();
  await page.keyboard.press("ControlOrMeta+A");
  await page.keyboard.press("Backspace");
  await page.keyboard.type(keys);
}

interface ExportedDates {
  format: string;
  sections: {
    experience: {
      employers: { start: string; end: string | null }[];
      education: { start: string | null; end: string | null }[];
    };
  };
}

let context: BrowserContext;
let page: Page;
let errors: string[];
let exported: string;
const work = fs.mkdtempSync(path.join(os.tmpdir(), "jobapp-p7-"));

test.beforeAll(async ({ browser }) => {
  context = await newContext(browser);
  page = await context.newPage();
  errors = watchErrors(page);
});

test.afterAll(async () => {
  await context.close();
  clearAttempts([EMAIL]);
  fs.rmSync(work, { recursive: true, force: true });
});

test("an employer and a school with years only are saved and read back as entered", async () => {
  await page.goto(cliLink(EMAIL));
  await setPassword(page, PASSWORD, NAME);
  await page.goto("/profile?tab=experience");
  await expect(tab(page, "Experience")).toHaveAttribute(
    "aria-selected",
    "true",
  );

  await page.getByRole("button", { name: "Add employer" }).click();
  await field(page, "Organisation of employer 1").fill("Northwind Labs");
  await field(page, "Job title at employer 1").fill("Software Engineer");
  const start = field(page, "Start of employer 1");
  const end = field(page, "End of employer 1");
  await expect(start).toHaveAttribute("placeholder", "YYYY or YYYY-MM");
  // The line under the field is its description: a screen reader says it with the label.
  await expect(start).toHaveAccessibleDescription(
    "A year alone or a month: 2021 or 2021-03.",
  );
  await start.pressSequentially("2021");
  await expect(start).toHaveAccessibleDescription("Read as the year 2021.");
  await end.pressSequentially("2023");
  await expect(end).toHaveAccessibleDescription("Read as the year 2023.");

  await page.getByRole("button", { name: "Add school" }).click();
  await field(page, "School 1").fill("École Exemple");
  await field(page, "Degree at school 1").fill("MSc Computer Science");
  await field(page, "Start of school 1").pressSequentially("2019");
  await expect(field(page, "End of school 1")).toHaveValue("");
  await shots(page, "experience-years-typed");

  await save(page);
  await expect(tab(page, "Experience")).toHaveAccessibleName(
    "Experience, 100% complete",
  );

  // After a reload: the four characters that were typed, no month made up for them.
  await page.goto("/profile?tab=experience");
  await expect(field(page, "Start of employer 1")).toHaveValue("2021");
  await expect(field(page, "End of employer 1")).toHaveValue("2023");
  await expect(field(page, "Start of school 1")).toHaveValue("2019");
  await expect(field(page, "End of school 1")).toHaveValue("");
  await expect(field(page, "Start of employer 1")).toHaveAccessibleDescription(
    "Read as the year 2021.",
  );
  await shots(page, "experience-years-reloaded");
  expect(errors).toEqual([]);
});

test("at 375 px, from the keyboard: the two refusals, then a year becomes a month", async () => {
  await page.setViewportSize({ width: 375, height: 812 });

  // Neither a year nor a month: refused with the sentence, before any request.
  await retype(page, "Start of employer 1", "2021-3");
  await page.getByRole("button", { name: "Save experience" }).click();
  const format = page.getByText(
    "Write the date as YYYY or YYYY-MM, between 1900 and 2100.",
  );
  await expect(format).toBeVisible();
  await expect(field(page, "Start of employer 1")).toHaveAttribute(
    "aria-invalid",
    "true",
  );

  // An end that is certainly before the start. 2021 to 2020-12 cannot be in order.
  await retype(page, "Start of employer 1", "2021");
  await retype(page, "End of employer 1", "2020-12");
  await page.getByRole("button", { name: "Save experience" }).click();
  await expect(page.getByText("The end is before the start.")).toBeVisible();
  await expect(format).toHaveCount(0);
  await shots(page, "experience-end-before-start");
  await page.setViewportSize({ width: 375, height: 812 });

  // A start of 2021-03 with an end of 2021 is in order: the end may be December.
  await retype(page, "End of employer 1", "2021");
  // Six digits, as a phone's number pad types them: the hyphen comes by itself.
  await retype(page, "Start of employer 1", "202103");
  await expect(field(page, "Start of employer 1")).toHaveValue("2021-03");
  await expect(field(page, "Start of employer 1")).toHaveAccessibleDescription(
    "Read as March 2021.",
  );
  await expect(page.getByText("The end is before the start.")).toHaveCount(0);
  await save(page);
  await retype(page, "End of employer 1", "2023");
  await save(page);

  await page.goto("/profile?tab=experience");
  await expect(field(page, "Start of employer 1")).toHaveValue("2021-03");
  await expect(field(page, "End of employer 1")).toHaveValue("2023");
  await expect(field(page, "Start of school 1")).toHaveValue("2019");
  await shots(page, "experience-year-and-month");
  expect(errors).toEqual([]);
});

test("the History drawer shows the year made a month as a change of that field", async () => {
  await page.getByRole("button", { name: "History of experience" }).click();
  const drawer = page.getByRole("dialog", { name: "Experience history" });
  await expect(drawer).toBeVisible();
  const versions = drawer.getByRole("navigation", {
    name: "Versions of experience",
  });
  // Three saves: years only, the month with the end 2021, the end back to 2023.
  await expect(versions.getByRole("button")).toHaveCount(3);
  await versions.getByRole("button", { name: /^Revision 1, saved/ }).click();
  const table = drawer.getByRole("table", {
    name: "Revision 1 and the current version",
  });
  await expect(
    drawer.getByText("1 field differs from the current version."),
  ).toBeVisible();
  const row = table.getByRole("row", { name: /^Employer 1 › Start/ });
  await expect(row).toHaveAttribute("data-status", "changed");
  await expect(row.getByRole("cell")).toHaveText(["2021", "2021-03"]);
  const unchanged = table.getByRole("row", { name: /^Employer 1 › End/ });
  await expect(unchanged).toHaveAttribute("data-status", "same");
  await expect(unchanged.getByRole("cell")).toHaveText(["2023", "2023"]);
  await expect(
    table.getByRole("row", { name: /^School 1 › Start/ }).getByRole("cell"),
  ).toHaveText(["2019", "2019"]);
  await shots(page, "experience-history-year-to-month", false);
  await page.keyboard.press("Escape");
  await expect(drawer).toBeHidden();
  expect(errors).toEqual([]);
});

test("export, change, import the export back: the year alone comes back as written", async () => {
  await page.getByRole("button", { name: "Export or import" }).click();
  const download = page.waitForEvent("download");
  await page.getByRole("menuitem", { name: "Export profile" }).click();
  const file = await download;
  exported = path.join(work, file.suggestedFilename());
  await file.saveAs(exported);
  const text = fs.readFileSync(exported, "utf8");
  const document = JSON.parse(text) as ExportedDates;
  expect(document.format).toBe("jobapp-profile");
  expect(document.sections.experience.employers[0]).toMatchObject({
    start: "2021-03",
    end: "2023",
  });
  expect(document.sections.experience.education[0]).toMatchObject({
    start: "2019",
    end: null,
  });
  // The file as written: the year between quotes, with no month after it.
  expect(text).toContain('"start": "2019",');
  expect(text).toContain('"end": "2023",');
  expect(text).not.toContain("2019-");
  expect(text).not.toContain("2023-");

  // Give the school a month and an end, so the import has something to bring back.
  await field(page, "Start of school 1").fill("2019-09");
  await field(page, "End of school 1").fill("2021");
  await save(page);

  await page.getByRole("button", { name: "Export or import" }).click();
  await page.getByRole("menuitem", { name: "Import profile" }).click();
  const dialog = page.getByRole("dialog", { name: "Import profile" });
  await dialog.getByLabel("Profile file").setInputFiles(exported);
  const summary = dialog.getByRole("region", {
    name: "What the import replaces",
  });
  await expect(summary.getByRole("listitem").nth(1)).toHaveText(
    /^Experience: 1 employer, 1 schoolReplaced$/,
  );
  await dialog.getByRole("button", { name: "Replace my profile" }).click();
  await expect(page.getByText("Profile imported.")).toBeVisible();
  await expect(dialog).toBeHidden();

  await page.goto("/profile?tab=experience");
  await expect(field(page, "Start of employer 1")).toHaveValue("2021-03");
  await expect(field(page, "End of employer 1")).toHaveValue("2023");
  await expect(field(page, "Start of school 1")).toHaveValue("2019");
  await expect(field(page, "End of school 1")).toHaveValue("");
  await expect(field(page, "Start of school 1")).toHaveAccessibleDescription(
    "Read as the year 2019.",
  );
  expect(errors).toEqual([]);
});
