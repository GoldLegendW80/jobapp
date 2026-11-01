import { expect, test, type BrowserContext, type Page } from "@playwright/test";

import { waitForPostingDocuments } from "./apply-document-helpers";
import { atsFormsUrl, stub } from "./apply-helpers";
import { setReviewBeforeSend } from "./apply-one-click-helpers";
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
 * One click Apply (issue #738) against the real stack through Caddy plus the fake ATS forms
 * server. The owner's words: "when I click on apply, it open a browser, navigate to the job
 * offer, fill in the form and click on apply. I want a checkbox in settings to set if I want to
 * review the form filled. By default its no (full auto)".
 *
 * An invented person with the consent, a base CV and two postings on the plain fake Greenhouse
 * forms (nothing a profile leaves, no free text, so no check by model is needed), each with its
 * CV and letter written by the documents pipeline on the fake backend:
 *
 * - the review box unticked (the default, saved explicitly): one click on Apply, and the page
 *   follows the fill and the send live until it says "Sent."; the fake holds exactly one
 *   accepted submission for that form, and no Approve step was offered;
 * - the review box ticked: Apply stops at the filled form with "Approve and submit", and the
 *   fake holds no submission for that form.
 *
 * Locally, as apply-all.config.ts says (the fake forms compose file, AGENTS_FAKE_BACKEND=1 on
 * the worker, the stack's browser up with COMPOSE_PROFILES=browser), then:
 *   E2E_CADDY_URL=... E2E_ATS_FORMS_URL=... E2E_COMPOSE_PROJECT=... \
 *     pnpm --dir web exec playwright test -c e2e/apply-all.config.ts apply-one-click.spec.ts
 */
// One fill of about 100 s and one submit lease of about 120 s (over 200 s on a loaded stack)
// after the documents are written, in the first test; one fill in the second.
test.describe.configure({ mode: "serial", timeout: 1_200_000 });
test.skip(!caddyUrl, "set E2E_CADDY_URL with the Compose stack running");

/** The fake forms server as the stack's browser reaches it (tests/ats-forms-stub.compose.yml). */
const STACK_FORMS = "http://atsforms:8080";
const KEY = `sk-ant-api03-e2egood${"Oc738".repeat(16)}Rn42`;
const FILL_MS = 420_000;
const SEND_MS = 600_000;

/** The fake Greenhouse form of each posting (one job each), and what its title adds to the role. */
const FORMS = {
  direct: { job: "4012346", suffix: ", Fleet" },
  review: { job: "4012347", suffix: ", Controls" },
} as const;
type Key = keyof typeof FORMS;

let person: Person;
let context: BrowserContext;
let page: Page;
let errors: string[];

/** A posting of the person whose address is the plain fake form of `key`, with its documents. */
async function postingOn(key: Key): Promise<string> {
  const job = await addPosting(page.request, {
    ...person,
    posting: {
      ...person.posting,
      title: person.posting.title + FORMS[key].suffix,
    },
  });
  const posting = (
    await stub.posting("greenhouse", "plain", FORMS[key].job)
  ).replace(atsFormsUrl ?? "", STACK_FORMS);
  const read = await page.request.get(`/api/jobs/${job}`);
  const { updated_at } = (await read.json()) as { updated_at: string };
  const patched = await page.request.patch(`/api/jobs/${job}`, {
    headers: CSRF,
    data: { url: posting, fit: 90, updated_at },
  });
  expect(patched.status()).toBe(200);
  const written = await page.request.post(`/api/jobs/${job}/documents/write`, {
    headers: CSRF,
    data: { kinds: ["cv", "letter"], script: "journey" },
  });
  expect(written.status()).toBe(202);
  await waitForPostingDocuments(page.request, job);
  return job;
}

/** The submissions the fake recorded for the form of `key`, accepted ones only. */
async function sentTo(key: Key) {
  return (await stub.submissions()).filter(
    (one) =>
      one.ats === "greenhouse" && one.job === FORMS[key].job && one.accepted,
  );
}

type Application = { id: string; state: string; mode: string };
async function latest(job: string): Promise<Application> {
  const response = await page.request.get(
    `/api/applications?posting_id=${job}&limit=1`,
  );
  const { items } = (await response.json()) as { items: Application[] };
  return items[0] ?? { id: "", state: "absent", mode: "" };
}

test.beforeAll(async ({ browser }) => {
  person = invented("oneclick");
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
  // A new account has the review box unticked: Apply fills the form and sends it.
  const settings = await page.request.get("/api/apply/settings");
  expect(
    ((await settings.json()) as { review_before_send: boolean })
      .review_before_send,
  ).toBe(false);
});

test("box unticked: one click on Apply fills the form and sends it, exactly once", async () => {
  await setReviewBeforeSend(page.request, false);
  const job = await postingOn("direct");
  await page.goto(`/jobs/${job}`);
  const section = page.getByRole("region", { name: "Apply", exact: true });
  await expect(section).toContainText(
    "The assistant fills the form and sends it. You can watch it live here.",
  );
  await section.getByRole("button", { name: "Apply", exact: true }).click();
  await expect
    .poll(async () => (await latest(job)).mode, { timeout: 30_000 })
    .toBe("direct");

  // The worker's tick starts the fill; the page follows the fill, the check and the send by
  // itself, with no Approve step, until the status line says it was sent.
  await expect(
    section.getByRole("status").filter({ hasText: /^Sent\./ }),
  ).toBeVisible({ timeout: FILL_MS + SEND_MS });
  expect(await latest(job)).toMatchObject({
    state: "submitted",
    mode: "direct",
  });
  await expect(
    section.getByRole("button", { name: "Approve and submit" }),
  ).toHaveCount(0);
  // What was sent stays on the page: the fields and the page after the click.
  await expect(
    section.getByRole("list", { name: "Fields filled" }),
  ).toBeVisible();
  await expect(
    section.getByRole("img", { name: /after the click/ }),
  ).toBeVisible();
  expect(await sentTo("direct")).toHaveLength(1);
});

test("box ticked: Apply stops at the filled form, and nothing is sent", async () => {
  await setReviewBeforeSend(page.request, true);
  const job = await postingOn("review");
  await page.goto(`/jobs/${job}`);
  const section = page.getByRole("region", { name: "Apply", exact: true });
  await expect(section).toContainText(
    "The assistant fills the form, then you check it and send it.",
  );
  await section.getByRole("button", { name: "Apply", exact: true }).click();
  await expect(
    section.getByRole("button", { name: "Approve and submit", exact: true }),
  ).toBeVisible({ timeout: FILL_MS });
  expect(await latest(job)).toMatchObject({ state: "ready", mode: "review" });
  expect(await sentTo("review")).toHaveLength(0);
  // The first test's submission is still the only one.
  expect(await sentTo("direct")).toHaveLength(1);
});

test("no page error was logged", () => {
  expect(errors).toEqual([]);
});
