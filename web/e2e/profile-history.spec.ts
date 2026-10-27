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

const takeShots = makeShots("profile-p6", "history");

async function shots(page: Page, name: string, fullPage = true) {
  await expect(page.locator("[data-sonner-toast]")).toHaveCount(0, {
    timeout: 15_000,
  });
  await takeShots(page, name, fullPage);
}

/**
 * Profile P6 against the real stack through Caddy, no mocks, one fresh account and only made up
 * data: it fills a profile from empty to 100% in the browser (no JSON, no CLI), edits a section
 * twice, opens its History, compares and restores the first version, exports the profile,
 * changes it, imports the export back and finds the data and the completeness restored.
 *
 * Locally, on a scratch Compose project (its own volumes, torn down afterwards):
 *   HTTP_PORT=8096 docker compose -p jobapp-p6 -f compose.yml -f compose.mac.yml up -d --build --wait
 *   docker compose -p jobapp-p6 -f compose.yml exec -T api alembic upgrade head
 *   E2E_CADDY_URL=http://127.0.0.1:8096 E2E_COMPOSE_PROJECT=jobapp-p6 \
 *     pnpm --dir web exec playwright test profile-history.spec.ts
 *   docker compose -p jobapp-p6 -f compose.yml down -v
 *
 * On the Jetson it runs inside web/e2e/identity-all.config.ts (tests/e2e_jetson.sh). In CI the api
 * asks the ATS stub (tests/ats_stub.py, tests/ats-stub.compose.yml) instead of the real Ashby.
 */
test.describe.configure({ mode: "serial", timeout: 180_000 });
test.skip(!caddyUrl, "set E2E_CADDY_URL with the Compose stack running");

const run = Date.now().toString(36);
const EMAIL = `e2e-history-${run}@example.org`;
const NAME = "Robin Example";
const PASSWORD = `history journey ${run} passphrase`;
const FIRST = "Applied AI Engineer";
const SECOND = "Staff AI Engineer";

const tab = (page: Page, name: string) =>
  page.getByRole("tab", { name: new RegExp(`^${name}, \\d+% complete$`) });

async function openTab(page: Page, name: string) {
  await tab(page, name).click();
  await expect(tab(page, name)).toHaveAttribute("aria-selected", "true");
}

async function save(page: Page, section: string) {
  await page
    .getByRole("button", { name: `Save ${section.toLowerCase()}` })
    .click();
  await expect(page.getByText(`${section} saved.`).first()).toBeVisible();
  await expect(tab(page, section)).toHaveAccessibleName(
    `${section}, 100% complete`,
  );
}

const completeness = (page: Page) =>
  page.getByRole("progressbar", { name: "Profile completeness" });

async function addTerm(page: Page, label: string, term: string) {
  await page.getByLabel(`New term for ${label}`, { exact: true }).fill(term);
  await page.keyboard.press("Enter");
  await expect(
    page.getByRole("list", { name: `Terms of ${label}` }),
  ).toContainText(term);
}

let context: BrowserContext;
let page: Page;
let errors: string[];
let exported: string;
const work = fs.mkdtempSync(path.join(os.tmpdir(), "jobapp-p6-"));

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

test("a new account fills its profile from empty to 100% in the browser", async () => {
  await page.goto(cliLink(EMAIL));
  await setPassword(page, PASSWORD, NAME);
  await page.goto("/profile");
  await expect(completeness(page)).toHaveAttribute("aria-valuenow", "0");

  // Identity
  await page.getByLabel("Full name", { exact: true }).fill(NAME);
  await page.getByLabel("Headline", { exact: true }).fill(FIRST);
  await page.getByLabel("Email", { exact: true }).fill(EMAIL);
  await page.getByLabel("Phone", { exact: true }).fill("+33 6 00 00 00 00");
  await page.getByLabel("City", { exact: true }).fill("Bordeaux");
  await page.getByLabel("Country", { exact: true }).fill("France");
  await page.getByRole("button", { name: "Add language" }).click();
  await page.getByLabel("Language 1", { exact: true }).fill("French");
  await save(page, "Identity");

  // Experience
  await openTab(page, "Experience");
  await page.getByRole("button", { name: "Add employer" }).click();
  await page
    .getByLabel("Organisation of employer 1", { exact: true })
    .fill("Acme Robotics");
  await page
    .getByLabel("Job title at employer 1", { exact: true })
    .fill("AI Engineer");
  await page.getByLabel("Start of employer 1", { exact: true }).fill("2024-03");
  await page.getByLabel("Present at employer 1", { exact: true }).check();
  await page.getByRole("button", { name: "Add school" }).click();
  await page.getByLabel("School 1", { exact: true }).fill("École Exemple");
  await page.getByLabel("Degree at school 1", { exact: true }).fill("MSc");
  await save(page, "Experience");

  // Facts
  await openTab(page, "Facts");
  await page.getByRole("button", { name: "Add block" }).click();
  await page.getByLabel("Title of block 1", { exact: true }).fill("HARD RULES");
  await page
    .getByLabel("Text of block 1", { exact: true })
    .fill("1. Every claim traces to this base.\n2. No invented metric.");
  await save(page, "Facts");

  // Skills
  await openTab(page, "Skills");
  await page.getByLabel("Years of experience", { exact: true }).fill("4");
  await page
    .getByRole("button", { name: "Add category to competencies you have" })
    .click();
  await page
    .getByLabel("Name of have category 1", { exact: true })
    .fill("agents");
  await addTerm(page, "agents", "agent");
  await save(page, "Skills");

  // Search, with a board checked against the real Ashby API by the api
  await openTab(page, "Search");
  await addTerm(page, "Job titles", "ai engineer");
  await addTerm(page, "Places", "Paris");
  await page.getByRole("button", { name: "CDI (permanent)" }).click();
  await page
    .getByLabel("Remote stance", { exact: true })
    .selectOption("hybrid");
  await page
    .getByLabel("New board company", { exact: true })
    .fill("Mistral AI");
  await page.getByLabel("New board ATS", { exact: true }).selectOption("ashby");
  await page.getByLabel("New board org", { exact: true }).fill("mistral.ai");
  await page.getByRole("button", { name: "Check", exact: true }).click();
  await expect(
    page.getByRole("region", { name: "Add company" }).getByRole("status"),
  ).toContainText(/^Found on Ashby/, { timeout: 20_000 });
  await page.getByRole("button", { name: "Add board" }).click();
  await save(page, "Search");

  // Answers
  await openTab(page, "Answers");
  await page.getByRole("button", { name: "Add country" }).click();
  await page.getByLabel("Country or region 1", { exact: true }).fill("France");
  await page
    .getByLabel("Answer for country 1", { exact: true })
    .fill("French citizen, no sponsorship needed.");
  await page.getByLabel("Notice period", { exact: true }).fill("One month.");
  await page.getByLabel("Earliest start", { exact: true }).fill("January 2027");
  await page
    .getByLabel("Salary policy", { exact: true })
    .fill("Against the band for the role.");
  await page
    .getByLabel("Motivation", { exact: true })
    .fill("I turn prototypes into products.");
  await save(page, "Answers");

  await expect(completeness(page)).toHaveAttribute("aria-valuenow", "100");
  expect(errors).toEqual([]);
});

test("edit a section twice, compare in History, restore the first version", async () => {
  await openTab(page, "Identity");
  const headline = page.getByLabel("Headline", { exact: true });
  await headline.fill(SECOND);
  await page.getByLabel("City", { exact: true }).fill("Paris");
  await save(page, "Identity"); // revision 7

  await page.getByRole("button", { name: "History of identity" }).click();
  const drawer = page.getByRole("dialog", { name: "Identity history" });
  await expect(drawer).toBeVisible();
  const versions = drawer.getByRole("navigation", {
    name: "Versions of identity",
  });
  await expect(versions.getByRole("button")).toHaveCount(2);
  await expect(
    versions.getByRole("button", { name: /^Revision 7, current, saved/ }),
  ).toBeVisible();
  await versions.getByRole("button", { name: /^Revision 1, saved/ }).click();
  const table = drawer.getByRole("table", {
    name: "Revision 1 and the current version",
  });
  await expect(
    drawer.getByText("2 fields differ from the current version."),
  ).toBeVisible();
  const row = table.getByRole("row", { name: /^Headline/ });
  await expect(row).toHaveAttribute("data-status", "changed");
  await expect(row).toContainText(FIRST);
  await expect(row).toContainText(SECOND);
  await expect(table.getByRole("row", { name: /^City/ })).toContainText(
    "Bordeaux",
  );
  await shots(page, "identity-history-diff", false);

  await drawer.getByRole("button", { name: "Restore this version" }).click();
  await expect(
    page.getByText("Revision 1 of identity restored as revision 8."),
  ).toBeVisible();
  await expect(
    versions.getByRole("button", { name: /^Revision 8, current, saved/ }),
  ).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(drawer).toBeHidden();
  await expect(headline).toHaveValue(FIRST);
  await expect(page.getByLabel("City", { exact: true })).toHaveValue(
    "Bordeaux",
  );

  // The fact base compares line by line.
  await openTab(page, "Facts");
  await page.getByRole("button", { name: "HARD RULES, block 1" }).click();
  await page
    .getByLabel("Text of block 1", { exact: true })
    .fill("1. Every claim traces to this base.\n3. Lead with the business.");
  await save(page, "Facts"); // revision 9
  await page.getByRole("button", { name: "History of facts" }).click();
  const factsDrawer = page.getByRole("dialog", { name: "Facts history" });
  await factsDrawer.getByRole("button", { name: /^Revision 3, saved/ }).click();
  const lines = factsDrawer.getByRole("list", {
    name: "Lines of the fact base",
  });
  await expect(lines.locator('[data-kind="added"]')).toHaveText([
    "Only in revision 3: 2. No invented metric.",
  ]);
  await expect(lines.locator('[data-kind="removed"]')).toHaveText([
    "Only in the current version: 3. Lead with the business.",
  ]);
  await shots(page, "facts-history-diff", false);
  await page.keyboard.press("Escape");
  await expect(factsDrawer).toBeHidden();

  // After a reload the restored version is still there.
  await page.goto("/profile?tab=identity");
  await expect(page.getByLabel("Headline", { exact: true })).toHaveValue(FIRST);
  expect(errors).toEqual([]);
});

test("export, change, import the export back: data and completeness restored", async () => {
  await page.getByRole("button", { name: "Export or import" }).click();
  const download = page.waitForEvent("download");
  await page.getByRole("menuitem", { name: "Export profile" }).click();
  const file = await download;
  expect(file.suggestedFilename()).toMatch(/^profile-\d{4}-\d{2}-\d{2}\.json$/);
  exported = path.join(work, file.suggestedFilename());
  await file.saveAs(exported);
  const document = JSON.parse(fs.readFileSync(exported, "utf8")) as {
    format: string;
    version: number;
    sections: {
      identity: { headline: string; city: string };
      answers: { motivation: string };
    };
  };
  expect(document.format).toBe("jobapp-profile");
  expect(document.version).toBe(1);
  expect(document.sections.identity.headline).toBe(FIRST);
  expect(document.sections.answers.motivation).toBe(
    "I turn prototypes into products.",
  );

  // Change two sections; the answers lose a required field.
  await page.getByLabel("City", { exact: true }).fill("Marseille");
  await save(page, "Identity");
  await openTab(page, "Answers");
  await page.getByLabel("Motivation", { exact: true }).fill("");
  await page.getByRole("button", { name: "Save answers" }).click();
  await expect(page.getByText("Answers saved.").first()).toBeVisible();
  await expect(completeness(page)).toHaveAttribute("aria-valuenow", "97");

  // A file that is not an export is refused in the browser.
  await page.getByRole("button", { name: "Export or import" }).click();
  await page.getByRole("menuitem", { name: "Import profile" }).click();
  const dialog = page.getByRole("dialog", { name: "Import profile" });
  const input = dialog.getByLabel("Profile file");
  await input.setInputFiles({
    name: "notes.json",
    mimeType: "application/json",
    buffer: Buffer.from('{"format": "something-else", "version": 1}'),
  });
  await expect(dialog.getByText("This file cannot be imported")).toBeVisible();
  await expect(
    dialog.getByRole("button", { name: "Replace my profile" }),
  ).toBeDisabled();
  await shots(page, "import-refused", false);

  // The export: a summary of what it replaces, then the import.
  await input.setInputFiles(exported);
  const summary = dialog.getByRole("region", {
    name: "What the import replaces",
  });
  await expect(summary).toBeVisible();
  await expect(summary.getByRole("listitem")).toHaveText([
    /^Identity: Robin Example, 1 languageReplaced$/,
    /^Experience: 1 employer, 1 schoolUnchanged$/,
    /^Facts: 1 blockUnchanged$/,
    /^Skills: 1 category, 0 gatesUnchanged$/,
    /^Search: 1 board, 1 enabledUnchanged$/,
    /^Answers: 1 countryReplaced$/,
    // Never touched by this journey: the defaults, exported and imported back unchanged.
    /^Preferences: Documents: language auto, tone plainUnchanged$/,
    /^Photo: none in the fileKept$/,
  ]);
  await expect(summary).toContainText(
    "Completeness after the import: 100% (now 97%)",
  );
  await shots(page, "import-summary", false);
  await dialog.getByRole("button", { name: "Replace my profile" }).click();
  await expect(page.getByText("Profile imported.")).toBeVisible();
  await expect(dialog).toBeHidden();
  await expect(completeness(page)).toHaveAttribute("aria-valuenow", "100");
  await expect(page.getByLabel("Motivation", { exact: true })).toHaveValue(
    "I turn prototypes into products.",
  );

  // Persisted: a reload shows the imported data, and the history records the import.
  await page.goto("/profile?tab=identity");
  await expect(page.getByLabel("City", { exact: true })).toHaveValue(
    "Bordeaux",
  );
  await expect(page.getByLabel("Headline", { exact: true })).toHaveValue(FIRST);
  await expect(completeness(page)).toHaveAttribute("aria-valuenow", "100");
  await page.getByRole("button", { name: "History of identity" }).click();
  const drawer = page.getByRole("dialog", { name: "Identity history" });
  // 6 fills, an identity edit, the restore, a facts edit, 2 edits (10, 11), then the import
  // saves the six sections as 12 to 17, identity first.
  await expect(
    drawer.getByRole("button", { name: /^Revision 12, current, saved/ }),
  ).toBeVisible();
  await shots(page, "identity-history", false);
  await page.keyboard.press("Escape");
  await shots(page, "identity-after-import");
  expect(errors).toEqual([]);
});
