import { expect, test, type BrowserContext, type Page } from "@playwright/test";

import { waitForPostingDocuments } from "./apply-document-helpers";
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
 * the worker), with the stack's browser up (COMPOSE_PROFILES=browser and a
 * BROWSER_CONTROL_SECRET in .env, as the local check's apply group has them), then:
 *   E2E_CADDY_URL=... E2E_ATS_FORMS_URL=... E2E_COMPOSE_PROJECT=... \
 *     pnpm --dir web exec playwright test -c e2e/apply-all.config.ts apply-a7.spec.ts
 */
// Each apply test holds two human paced browser runs (the fill, about 100 s, and the submit
// lease, which types the form again, about 120 s) after the documents are written: the budget
// is the sum of the waits below, not one of them.
test.describe.configure({ mode: "serial", timeout: 600_000 });
test.skip(!caddyUrl, "set E2E_CADDY_URL with the Compose stack running");

const TYPED = "Not applicable";
const PLACE = "Paris, France"; // one of the places of the fake Ashby location typeahead
/** The fake forms server as the stack's browser reaches it: APPLY_STUB_BASE_URL of the api and
 * the worker in tests/ats-forms-stub.compose.yml, which also lets it through the browser's proxy. */
const STACK_FORMS = "http://atsforms:8080";
const KEY = `sk-ant-api03-e2egood${"Dn9x".repeat(20)}Rn42`;

let person: Person;
let context: BrowserContext;
let page: Page;
let errors: string[];

/** A posting of the person whose address is the fake form of `ats`, with its two documents. */
async function postingOn(ats: "greenhouse" | "ashby"): Promise<string> {
  // A second posting of the same role at the same company is refused (a duplicate of the
  // posting, then same_role once the first is applied): the Ashby one is another team's role.
  const role = ats === "greenhouse" ? "" : ", Developer Tools";
  const job = await addPosting(page.request, {
    ...person,
    posting: { ...person.posting, title: person.posting.title + role },
  });
  // The posting's address is the one the stack's browser opens, not this test's loopback port.
  const posting = (await stub.posting(ats)).replace(
    atsFormsUrl ?? "",
    STACK_FORMS,
  );
  const read = await page.request.get(`/api/jobs/${job}`);
  const { updated_at } = (await read.json()) as { updated_at: string };
  const patched = await page.request.patch(`/api/jobs/${job}`, {
    headers: CSRF,
    data: { url: posting, fit: 90, updated_at },
  });
  expect(patched.status()).toBe(200);
  // The fake backend replays the recorded streams D12 made for this invented person
  // (documents_cv_journey, then documents_letter_journey); without a script it runs `echo`,
  // which submits no CV, and the generation ends no_result.
  const written = await page.request.post(`/api/jobs/${job}/documents/write`, {
    headers: CSRF,
    data: { kinds: ["cv", "letter"], script: "journey" },
  });
  expect(written.status()).toBe(202);
  await waitForPostingDocuments(page.request, job);
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

    // The worker's tick starts the fill within 30 seconds; the page follows it by itself. The
    // fill ends with the form before the click: waiting for the approval, or, when it left a
    // required question (the fake Greenhouse form asks for the work authorisation, which this
    // invented person never gave), waiting for that answer first.
    await expect(
      section.getByRole("img", { name: /before the click/ }),
    ).toBeVisible({ timeout: 180_000 });

    // Every field the assistant left that can be answered here is answered by the person: a
    // text box is typed and saved, a yes or no takes No, a list of choices its first choice. A
    // row with no control (a file, a skipped widget) is answered on the site and is not part of
    // the loop.
    const open = section.locator("li[data-left=true]").filter({
      has: page.locator("input, textarea, select, [role=group] button"),
    });
    let typed = 0;
    for (;;) {
      const before = await open.count();
      if (before === 0) break;
      const row = open.first();
      const box = row.getByRole("textbox");
      const choices = row.getByRole("combobox");
      if (await box.count()) {
        // The fake Ashby location typeahead takes only a place it lists (its options load as
        // one types, so the fill could not store them): any other text cannot be refilled.
        const place = /location/i.test(
          (await box.getAttribute("aria-label")) ?? "",
        );
        await box.fill(place ? PLACE : TYPED);
        await row.getByRole("button", { name: "Save answer" }).click();
        if (!place) typed += 1;
      } else if (await choices.count()) {
        await choices.selectOption({ index: 1 });
      } else {
        await row.getByRole("button", { name: "No", exact: true }).click();
      }
      await expect(open).toHaveCount(before - 1);
    }
    await expect(section).toContainText("waiting for your approval");

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
    // Edit then approve: the value the person typed is what the form received.
    if (typed > 0) {
      expect(Object.values(accepted[0]!.fields).flat()).toContain(TYPED);
    }
  });
}

test("no page error was logged", () => {
  expect(errors).toEqual([]);
});
