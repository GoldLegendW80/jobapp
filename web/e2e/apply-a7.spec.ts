import { expect, test, type BrowserContext, type Page } from "@playwright/test";

import { stub } from "./apply-helpers";
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
 * Apply A7 against the real stack through Caddy plus the fake ATS forms server: the first end to
 * end slice of the module. An invented person with the consent, a base CV and a posting whose
 * address is the fake Greenhouse posting (then the fake Ashby one) has its CV and letter written
 * by the documents pipeline on the fake backend, opens the job page, clicks Apply (Review first),
 * waits for the fill (the worker's 30 second tick starts it), types an answer for every field
 * the assistant left for them, approves behind the confirmation that names the company, the role
 * and both documents, and sees Applied, both screenshots and the run report. The fake server holds
 * exactly one accepted submission for that posting.
 *
 * Locally, as apply-all.config.ts says (the fake forms compose file, AGENTS_FAKE_BACKEND=1 on
 * the worker), then:
 *   E2E_CADDY_URL=... E2E_ATS_FORMS_URL=... E2E_COMPOSE_PROJECT=... \
 *     pnpm --dir web exec playwright test -c e2e/apply-all.config.ts apply-a7.spec.ts
 */
test.describe.configure({ mode: "serial", timeout: 240_000 });
test.skip(!caddyUrl, "set E2E_CADDY_URL with the Compose stack running");

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

/** A posting of the person whose address is the fake form of `ats`, with its two documents. */
async function postingOn(ats: "greenhouse" | "ashby"): Promise<string> {
  const job = await addPosting(page.request, person);
  const read = await page.request.get(`/api/jobs/${job}`);
  const { updated_at } = (await read.json()) as { updated_at: string };
  const patched = await page.request.patch(`/api/jobs/${job}`, {
    headers: CSRF,
    data: { url: await stub.posting(ats), fit: 90, updated_at },
  });
  expect(patched.status()).toBe(200);
  const written = await page.request.post(`/api/jobs/${job}/documents/write`, {
    headers: CSRF,
    data: { kinds: ["cv", "letter"] },
  });
  expect(written.status()).toBe(202);
  await expect.poll(() => documentsReady(job), { timeout: 150_000 }).toBe(true);
  return job;
}

test.beforeAll(async ({ browser }) => {
  person = invented("a7");
  context = await newContext(browser);
  page = await context.newPage();
  errors = watchErrors(page);
});

test.afterAll(async () => {
  await context.close();
  clearAttempts([person.email]);
});

test("an invented person has the consent and a base CV", async () => {
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

for (const ats of ["greenhouse", "ashby"] as const) {
  test(`apply on the fake ${ats} form: fill, answer what is left, approve, applied`, async () => {
    const job = await postingOn(ats);
    await page.goto(`/jobs/${job}`);
    const section = page.getByRole("region", { name: "Apply" });
    await section.getByRole("button", { name: "Apply (Review first)" }).click();

    // The worker's tick starts the fill within 30 seconds; the page follows it by itself.
    await expect(section).toContainText("waiting for your approval", {
      timeout: 180_000,
    });
    await expect(
      section.getByRole("img", { name: /before the click/ }),
    ).toBeVisible();

    // Every field the assistant left is answered by the person.
    const open = section.locator("li[data-left=true]");
    while ((await open.count()) > 0) {
      const row = open.first();
      await row.getByRole("textbox").fill("Not applicable");
      await row.getByRole("button", { name: "Save answer" }).click();
      await expect(row).toHaveCount(0);
    }

    await section.getByRole("button", { name: "Approve and submit" }).click();
    const dialog = page.getByRole("alertdialog");
    await expect(dialog).toContainText("Submit this application to");
    await expect(dialog).toContainText("CV version");
    await expect(dialog).toContainText("Letter version");
    await dialog.getByRole("button", { name: "Approve and submit" }).click();

    await expect(section).toContainText("Applied.", { timeout: 180_000 });
    await expect(
      section.getByRole("img", { name: /after the click/ }),
    ).toBeVisible();
    await expect(
      section.getByRole("region", { name: "Run report" }),
    ).toContainText("Submit clicked");
    const accepted = (await stub.submissions()).filter(
      (one) => one.ats === ats && one.accepted,
    );
    expect(accepted).toHaveLength(1);
  });
}

test("no page error was logged", () => {
  expect(errors).toEqual([]);
});
