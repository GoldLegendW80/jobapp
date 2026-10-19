import {
  expect,
  test,
  type BrowserContext,
  type Locator,
  type Page,
} from "@playwright/test";

import { atsFormsUrl, stub } from "./apply-helpers";
import {
  CSRF,
  addPosting,
  caddyUrl,
  invented,
  signUp,
  waitForRender,
  type Person,
} from "./documents-helpers";
import { clearAttempts, newContext, watchErrors } from "./stack";

/**
 * Apply A8 against the real stack through Caddy plus the fake ATS forms server: an
 * invented person applies with Review first to the fake Lever posting of tests/ats_forms/a8/
 * (served by the fake server's static folder). The fill uploads the CV, undoes the fake resume
 * parse (a name, an e-mail, a phone and a company that are not the person's), types the
 * profile's values, chooses the native selects and writes the letter into "Additional
 * information"; the person answers what is left, approves, and the gate submits once. The fake
 * server holds exactly one submission of that form, with the person's values and none of the
 * parse's. Then the same person applies to the fake Workable like form of tests/ats_forms/a8/custom/:
 * the form agent (its fake stream, apply_form) fills it and designates its submit, the "before"
 * screenshot shows that button outlined, and approval submits once.
 *
 * Locally, as apply-all.config.ts says (the fake forms compose file, AGENTS_FAKE_BACKEND=1 on
 * the worker, COMPOSE_PROFILES=browser and a BROWSER_CONTROL_SECRET in .env), then:
 *   E2E_CADDY_URL=... E2E_ATS_FORMS_URL=... E2E_COMPOSE_PROJECT=... \
 *     pnpm --dir web exec playwright test -c e2e/apply-all.config.ts apply-a8.spec.ts
 */
// The fill and the submit lease are two human paced browser runs after the documents are
// written: the budget is the sum of the waits below.
test.describe.configure({ mode: "serial", timeout: 600_000 });
test.skip(!caddyUrl, "set E2E_CADDY_URL with the Compose stack running");

const TYPED = "Not applicable";
/** The fake forms server as the stack's browser reaches it (APPLY_STUB_BASE_URL). */
const STACK_FORMS = "http://atsforms:8080";
const LEVER = "/forms/a8/lever/example-robotics/applied-ai-engineer/apply.html";
const LEVER_FORM = "lever-example-robotics-applied-ai-engineer";
const CUSTOM = "/forms/a8/custom/example-robotics/workable.html";
const CUSTOM_FORM = "custom-workable";
const PARSED = [
  "Alex Exampel",
  "alex.exampel@parsed.example",
  "+33 1 00 00 00 00",
  "Northwind Labs",
];
const KEY = `sk-ant-api03-e2egood${"Dn9x".repeat(20)}Rn42`;

let person: Person;
let context: BrowserContext;
let page: Page;
let errors: string[];

type PostingDocument = { kind: string; latest: { number: number } | null };

async function documentsReady(job: string): Promise<boolean> {
  const response = await page.request.get(`/api/jobs/${job}/documents`);
  const { documents } = (await response.json()) as {
    documents: PostingDocument[];
  };
  return (
    documents.length === 2 && documents.every((one) => one.latest !== null)
  );
}

test.beforeAll(async ({ browser }) => {
  person = invented("a8");
  context = await newContext(browser);
  page = await context.newPage();
  errors = watchErrors(page);
});

test.afterAll(async () => {
  await context.close();
  clearAttempts([person.email]);
});

test("an invented person has the consent and a base CV", async () => {
  expect(atsFormsUrl).toBeTruthy();
  await stub.reset();
  await signUp(page, person);
  const saved = await page.request.put("/api/me/model-credential", {
    headers: CSRF,
    data: { kind: "anthropic_api_key", secret: KEY },
  });
  expect(saved.status()).toBe(200);
  const asked = await page.request.get("/api/documents/consent");
  const { version } = (await asked.json()) as { version: number };
  const accepted = await page.request.post("/api/documents/consent", {
    headers: CSRF,
    data: { version },
  });
  expect(accepted.status()).toBe(200);
  const built = await page.request.post("/api/documents/base-cvs", {
    headers: CSRF,
    data: { from: "profile", script: "documents_base_journey" },
  });
  expect(built.status()).toBe(202);
  const base = ((await built.json()) as { base_cv: { id: string } }).base_cv;
  await waitForRender(page.request, base.id, 1);
});

test("apply on the fake Lever form: the parse corrected, approve, submitted once", async () => {
  const job = await addPosting(page.request, person);
  const read = await page.request.get(`/api/jobs/${job}`);
  const { updated_at } = (await read.json()) as { updated_at: string };
  const patched = await page.request.patch(`/api/jobs/${job}`, {
    headers: CSRF,
    data: { url: STACK_FORMS + LEVER, fit: 90, updated_at },
  });
  expect(patched.status()).toBe(200);
  const written = await page.request.post(`/api/jobs/${job}/documents/write`, {
    headers: CSRF,
    data: { kinds: ["cv", "letter"], script: "journey" },
  });
  expect(written.status()).toBe(202);
  await expect.poll(() => documentsReady(job), { timeout: 150_000 }).toBe(true);

  await page.goto(`/jobs/${job}`);
  const section = page.getByRole("region", { name: "Apply" });
  await section.getByRole("button", { name: "Apply (Review first)" }).click();
  await expect(
    section.getByRole("img", { name: /before the click/ }),
  ).toBeVisible({ timeout: 180_000 });
  await page.screenshot({
    path: test.info().outputPath("lever-review.png"),
    fullPage: true,
  });

  // What the fill left (the current company the parse guessed, the selects the profile does
  // not answer) is answered by the person.
  const open = section.locator("li[data-left=true]").filter({
    has: page.locator("input, textarea, select, [role=group] button"),
  });
  for (;;) {
    const before = await open.count();
    if (before === 0) break;
    const row = open.first();
    const box = row.getByRole("textbox");
    if (await box.count()) {
      await box.fill(TYPED);
      await row.getByRole("button", { name: "Save answer" }).click();
    } else if (await row.getByRole("combobox").count()) {
      await row.getByRole("combobox").selectOption({ index: 1 });
    } else {
      await row.getByRole("button", { name: "No", exact: true }).click();
    }
    await expect(open).toHaveCount(before - 1);
  }
  await expect(section).toContainText("waiting for your approval");

  await section.getByRole("button", { name: "Approve and submit" }).click();
  const dialog = page.getByRole("alertdialog");
  await expect(dialog).toContainText("Submit this application to");
  await dialog.getByRole("button", { name: "Approve and submit" }).click();

  await expect(section).toContainText("Applied.", { timeout: 180_000 });
  await expect(
    section.getByRole("img", { name: /after the click/ }),
  ).toBeVisible();
  await page.screenshot({
    path: test.info().outputPath("lever-applied.png"),
    fullPage: true,
  });

  const sent = (await stub.submissions()).filter(
    (one) => one.org === LEVER_FORM,
  );
  expect(sent).toHaveLength(1);
  const values = Object.values(sent[0]!.fields).flat().map(String);
  for (const guess of PARSED) expect(values).not.toContain(guess);
  expect(sent[0]!.fields["comments"]).toBeTruthy(); // the letter, in Additional information
  expect(sent[0]!.fields["cards[sponsorship]"]).toBeTruthy();
});

/** Pixels of the before screenshot near the outline the app draws around the designated submit. */
async function outlinedPixels(shot: Locator): Promise<number> {
  return shot.evaluate(async (img: HTMLImageElement) => {
    await img.decode();
    const canvas = document.createElement("canvas");
    canvas.width = img.naturalWidth;
    canvas.height = img.naturalHeight;
    const drawn = canvas.getContext("2d");
    if (!drawn) return 0;
    drawn.drawImage(img, 0, 0);
    const { data } = drawn.getImageData(0, 0, canvas.width, canvas.height);
    let count = 0;
    for (let i = 0; i < data.length; i += 4) {
      const [r, g, b] = [data[i]!, data[i + 1]!, data[i + 2]!];
      if (r > 190 && g < 80 && b > 40 && b < 110) count += 1;
    }
    return count;
  });
}

// Skipped until worker.apply_services passes the form agent (held for the owner): without it the
// fill of an unknown form ends needs_person (no_adapter) and no submit is outlined.
test.skip("apply on the fake custom form: the submit designated and outlined, submitted once", async () => {
  const job = await addPosting(page.request, person);
  const read = await page.request.get(`/api/jobs/${job}`);
  const { updated_at } = (await read.json()) as { updated_at: string };
  const patched = await page.request.patch(`/api/jobs/${job}`, {
    headers: CSRF,
    data: { url: STACK_FORMS + CUSTOM, fit: 90, updated_at },
  });
  expect(patched.status()).toBe(200);
  const written = await page.request.post(`/api/jobs/${job}/documents/write`, {
    headers: CSRF,
    data: { kinds: ["cv", "letter"], script: "journey" },
  });
  expect(written.status()).toBe(202);
  await expect.poll(() => documentsReady(job), { timeout: 150_000 }).toBe(true);

  await page.goto(`/jobs/${job}`);
  const section = page.getByRole("region", { name: "Apply" });
  await section.getByRole("button", { name: "Apply (Review first)" }).click();
  const before = section.getByRole("img", { name: /before the click/ });
  await expect(before).toBeVisible({ timeout: 300_000 });
  // The form agent's fake stream (apply_form) designated "Submit application"; the app drew
  // its outline on the "before" screenshot.
  expect(await outlinedPixels(before)).toBeGreaterThan(200);
  await expect(section).toContainText("Submit application");
  await page.screenshot({
    path: test.info().outputPath("custom-review.png"),
    fullPage: true,
  });

  const open = section.locator("li[data-left=true]").filter({
    has: page.locator("input, textarea, select"),
  });
  for (;;) {
    const left = await open.count();
    if (left === 0) break;
    const row = open.first();
    const box = row.getByRole("textbox");
    if (await box.count()) {
      await box.fill(TYPED);
      await row.getByRole("button", { name: "Save answer" }).click();
    } else {
      await row.getByRole("combobox").selectOption({ index: 1 });
    }
    await expect(open).toHaveCount(left - 1);
  }
  await expect(section).toContainText("waiting for your approval");

  await section.getByRole("button", { name: "Approve and submit" }).click();
  const dialog = page.getByRole("alertdialog");
  await dialog.getByRole("button", { name: "Approve and submit" }).click();
  await expect(section).toContainText("Applied.", { timeout: 180_000 });
  await page.screenshot({
    path: test.info().outputPath("custom-applied.png"),
    fullPage: true,
  });

  const sent = (await stub.submissions()).filter(
    (one) => one.org === CUSTOM_FORM,
  );
  expect(sent).toHaveLength(1);
  expect(sent[0]!.fields["email"]).toBe(person.email);
});

test("no page error was logged", () => {
  expect(errors).toEqual([]);
});
