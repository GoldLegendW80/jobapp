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
 * data: it fills a profile from empty to 100% (no CLI; without a model key the skills and the
 * answers are fields of About you), edits a section twice,
 * opens its History from More actions, compares and restores the first version, exports the
 * profile, changes it, imports the export back and finds the data and the completeness restored.
 *
 * Locally, on a scratch Compose project (its own volumes, torn down afterwards):
 *   HTTP_PORT=8096 docker compose -p jobapp-p6 -f compose.yml -f compose.mac.yml up -d --build --wait
 *   docker compose -p jobapp-p6 -f compose.yml exec -T api alembic upgrade head
 *   E2E_CADDY_URL=http://127.0.0.1:8096 E2E_COMPOSE_PROJECT=jobapp-p6 \
 *     pnpm --dir web exec playwright test profile-history.spec.ts
 *   docker compose -p jobapp-p6 -f compose.yml down -v
 *
 * On the Jetson it runs inside web/e2e/identity-all.config.ts (tests/e2e_jetson.sh).
 */
test.describe.configure({ mode: "serial", timeout: 180_000 });
test.skip(!caddyUrl, "set E2E_CADDY_URL with the Compose stack running");

const run = Date.now().toString(36);
const EMAIL = `e2e-history-${run}@example.org`;
const NAME = "Robin Example";
const PASSWORD = `history journey ${run} passphrase`;
const FIRST = "Applied AI Engineer";
const SECOND = "Staff AI Engineer";

const CSRF = { "X-Requested-With": "jobapp" };

interface Stored {
  revision: number;
  sections: Record<string, Record<string, unknown> | null>;
  completeness: { sections: Record<string, { percent: number }> };
}

async function stored(page: Page): Promise<Stored> {
  const answer = await page.request.get("/api/profile");
  expect(answer.ok()).toBe(true);
  return (await answer.json()) as Stored;
}

/** Saves a section through the api, as Fill my profile or a reading of About you would. */
async function putSection(
  page: Page,
  section: string,
  change: (data: Record<string, unknown>) => Record<string, unknown>,
) {
  const profile = await stored(page);
  const saved = await page.request.put(`/api/profile/${section}`, {
    headers: CSRF,
    data: {
      base_revision: profile.revision,
      data: change(profile.sections[section] ?? {}),
    },
  });
  expect(saved.status(), await saved.text()).toBe(200);
}

async function save(page: Page, section: string) {
  await page
    .getByRole("button", { name: `Save ${section.toLowerCase()}` })
    .click();
  await expect(page.getByText(`${section} saved.`).first()).toBeVisible();
  expect(
    (await stored(page)).completeness.sections[section.toLowerCase()]?.percent,
  ).toBe(100);
}

const completeness = (page: Page) => page.getByTestId("profile-completeness");

/** The History drawer of one section, from the page's More actions. */
async function openHistory(page: Page, section: string) {
  await page.getByRole("button", { name: "More actions" }).click();
  await page.getByRole("menuitem", { name: "History" }).focus();
  await page.keyboard.press("ArrowRight");
  await page.getByRole("menuitem", { name: section, exact: true }).click();
  const drawer = page.getByRole("dialog", { name: `${section} history` });
  await expect(drawer).toBeVisible();
  return drawer;
}

const part = (page: Page, name: string) =>
  page.getByRole("region", { name, exact: true });

let context: BrowserContext;
let page: Page;
let errors: string[];
let exported: string;
const WEBSITE = "https://jobs.example-robotics.test/";
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

test("a new account fills its profile from empty to 100%", async () => {
  await page.goto(cliLink(EMAIL));
  await setPassword(page, PASSWORD, NAME);
  await page.goto("/profile/contact");
  await expect(completeness(page)).toHaveText("0% complete");

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
  await page.getByRole("tab", { name: "Experience" }).click();
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

  // Facts: a part of About you, saved to the profile at Done without a model.
  await page.getByRole("tab", { name: "About you" }).click();
  await page.getByRole("button", { name: "Add a part" }).click();
  await part(page, "New part")
    .getByLabel("Heading", { exact: true })
    .fill("HARD RULES");
  const rules = part(page, "HARD RULES");
  await rules
    .getByLabel("Text", { exact: true })
    .fill("1. Every claim traces to this base.\n2. No invented metric.");
  await expect(rules.getByText("Saved", { exact: true })).toBeVisible();
  await rules.getByRole("button", { name: "Done" }).click();
  await expect(rules.getByText("Applied to your profile:")).toBeVisible();
  expect((await stored(page)).completeness.sections.facts?.percent).toBe(100);

  // Skills: without a model key, About you shows them as fields (owner, 2026-11-07).
  const skills = page.getByRole("form", { name: "Skills" });
  await skills.getByLabel("Years of experience", { exact: true }).fill("4");
  await skills.getByRole("button", { name: /^What I have,/ }).click();
  await skills
    .getByRole("button", { name: "Add category to what I have" })
    .click();
  await skills
    .getByLabel("Name of category 1 of what I have", { exact: true })
    .fill("agents");
  const term = skills.getByLabel("New term for agents", { exact: true });
  await term.fill("agent");
  await term.press("Enter");
  await save(page, "Skills");

  // Search, in Find jobs: every search runs the job websites ticked there (owner, 2026-10-28),
  // and Search is complete with one of them. The website is invented (`.test` is reserved, no
  // DNS answers it); the search it starts on this stack, which has no browser, finds nothing.
  await page.goto("/jobs");
  await page.getByRole("button", { name: "Find jobs" }).click();
  const setup = page.getByRole("dialog", { name: "Find jobs" });
  await expect(setup).toBeVisible();
  await setup.getByLabel("Website address").fill(WEBSITE);
  await setup.getByRole("button", { name: "Add", exact: true }).click();
  await expect(setup).toContainText("jobs.example-robotics.test");
  for (const [label, term] of [
    ["Job titles", "ai engineer"],
    ["Places", "Paris"],
  ] as const) {
    const box = setup.getByRole("textbox", { name: `New term for ${label}` });
    await box.fill(term);
    await box.press("Enter");
    await expect(
      setup.getByRole("list", { name: `Terms of ${label}` }),
    ).toContainText(term);
  }
  // Contract and remote preference sit under More options.
  await setup.getByRole("button", { name: "More options" }).click();
  await setup.getByRole("checkbox", { name: "Permanent", exact: true }).check();
  await setup.getByLabel("Remote preference").selectOption("hybrid");
  await setup.getByRole("button", { name: "Save and start search" }).click();
  await expect(setup).toBeHidden({ timeout: 30_000 });
  expect((await stored(page)).completeness.sections.search?.percent).toBe(100);

  // Answers: like the skills, fields of About you without a model key.
  await page.goto("/profile/about");
  const answers = page.getByRole("form", { name: "Answers" });
  await answers.getByRole("button", { name: /^Work authorisation,/ }).click();
  await answers.getByRole("button", { name: "Add country" }).click();
  await answers
    .getByLabel("Country or region 1", { exact: true })
    .fill("France");
  await answers
    .getByLabel("Answer for country 1", { exact: true })
    .fill("French citizen, no sponsorship needed.");
  await answers
    .getByRole("button", { name: /^Availability and conditions,/ })
    .click();
  await answers.getByLabel("Notice period", { exact: true }).fill("One month.");
  await answers
    .getByLabel("Earliest start", { exact: true })
    .fill("January 2027");
  await answers
    .getByLabel("Salary policy", { exact: true })
    .fill("Against the band for the role.");
  await answers.getByRole("button", { name: /^In your own words,/ }).click();
  await answers
    .getByLabel("Motivation", { exact: true })
    .fill("I turn prototypes into products.");
  await save(page, "Answers");
  await expect(completeness(page)).toHaveText("100% complete");
  expect(errors).toEqual([]);
});

test("edit a section twice, compare in History, restore the first version", async () => {
  await page.getByRole("tab", { name: "Contact" }).click();
  const headline = page.getByLabel("Headline", { exact: true });
  await headline.fill(SECOND);
  await page.getByLabel("City", { exact: true }).fill("Paris");
  await save(page, "Identity"); // revision 7

  const drawer = await openHistory(page, "Identity");
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
  await page.getByRole("tab", { name: "About you" }).click();
  const rules = part(page, "HARD RULES");
  await rules.getByRole("button", { name: "Edit HARD RULES" }).click();
  await rules
    .getByLabel("Text", { exact: true })
    .fill("1. Every claim traces to this base.\n3. Lead with the business.");
  await expect(rules.getByText("Saved", { exact: true })).toBeVisible();
  await rules.getByRole("button", { name: "Done" }).click(); // revision 9
  await expect
    .poll(async () => (await stored(page)).revision, { timeout: 15_000 })
    .toBe(9);
  const factsDrawer = await openHistory(page, "Facts");
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
  await page.goto("/profile/contact");
  await expect(page.getByLabel("Headline", { exact: true })).toHaveValue(FIRST);
  expect(errors).toEqual([]);
});

test("export, change, import the export back: data and completeness restored", async () => {
  await page.getByRole("button", { name: "More actions" }).click();
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

  // Change two sections; the answers lose a required field (through the api, as above).
  await putSection(page, "answers", (answers) => ({
    ...answers,
    motivation: null,
  }));
  await page.reload();
  await page.getByLabel("City", { exact: true }).fill("Marseille");
  await save(page, "Identity");
  await expect(completeness(page)).toHaveText("97% complete");

  // A file that is not an export is refused in the browser.
  await page.getByRole("button", { name: "More actions" }).click();
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
    /^Search: 1 job website, 1 tickedUnchanged$/,
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
  await expect(completeness(page)).toHaveText("100% complete");
  await expect(page.getByLabel("City", { exact: true })).toHaveValue(
    "Bordeaux",
  );
  const imported = await stored(page);
  expect(imported.sections.answers?.motivation).toBe(
    "I turn prototypes into products.",
  );

  // Persisted: a reload shows the imported data, and the history records the import.
  await page.goto("/profile/contact");
  await expect(page.getByLabel("City", { exact: true })).toHaveValue(
    "Bordeaux",
  );
  await expect(page.getByLabel("Headline", { exact: true })).toHaveValue(FIRST);
  await expect(completeness(page)).toHaveText("100% complete");
  const drawer = await openHistory(page, "Identity");
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
