import { expect, test, type BrowserContext, type Page } from "@playwright/test";

import { waitForPostingDocuments } from "./apply-document-helpers";
import { stub } from "./apply-helpers";
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
 * Job board listings and no dead ends (owner, 2026-11-01: "When I'm here, how can I continue
 * applying ?? I fucking don't understand"). The posting's address is a fake job board's listing
 * (tests/ats_forms/board/listing.html on the fake forms server), not the employer's form:
 *
 * - its Apply link names the fake Greenhouse form: one click on Apply with the review box
 *   unticked follows the link (read, never clicked), fills the employer's form and sends it;
 *   the fake holds exactly one accepted submission for that form;
 * - its Apply button has no address (script only): the page says so in one sentence and offers
 *   "Apply on the site myself" and "I applied"; "I applied" moves the posting to applied, and
 *   the app sent nothing.
 *
 * Locally, as apply-all.config.ts says (the fake forms compose file, AGENTS_FAKE_BACKEND=1 on
 * the worker, the stack's browser up with COMPOSE_PROFILES=browser), then:
 *   E2E_CADDY_URL=... E2E_ATS_FORMS_URL=... E2E_COMPOSE_PROJECT=... \
 *     pnpm --dir web exec playwright test -c e2e/apply-all.config.ts apply-board-link.spec.ts
 */
test.describe.configure({ mode: "serial", timeout: 1_200_000 });
test.skip(!caddyUrl, "set E2E_CADDY_URL with the Compose stack running");

/** The fake forms server as the stack's browser reaches it (tests/ats-forms-stub.compose.yml). */
const STACK_FORMS = "http://atsforms:8080";
const KEY = `sk-ant-api03-e2egood${"Bl741".repeat(16)}Rn42`;
const FILL_MS = 420_000;
const SEND_MS = 600_000;
/** The employer's fake Greenhouse form behind the listing, used by no other journey. */
const JOB = "4012350";
const LISTING = `${STACK_FORMS}/board/greenhouse/northwind-labs/${JOB}`;

let person: Person;
let context: BrowserContext;
let page: Page;
let errors: string[];

/** A posting of the person at `url`, of the form's company, with its CV and letter written. */
async function postingAt(url: string, suffix: string): Promise<string> {
  const job = await addPosting(page.request, {
    ...person,
    posting: { ...person.posting, title: person.posting.title + suffix },
  });
  const read = await page.request.get(`/api/jobs/${job}`);
  const { updated_at } = (await read.json()) as { updated_at: string };
  // The listing's company, so one click Apply follows the link to that company's own form.
  const patched = await page.request.patch(`/api/jobs/${job}`, {
    headers: CSRF,
    data: { url, company: "Northwind Labs", fit: 90, updated_at },
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

async function sent() {
  return (await stub.submissions()).filter(
    (one) => one.ats === "greenhouse" && one.job === JOB && one.accepted,
  );
}

type Application = {
  id: string;
  state: string;
  mode: string;
  needs: string | null;
  form_url: string;
};
async function latest(job: string): Promise<Application> {
  const response = await page.request.get(
    `/api/applications?posting_id=${job}&limit=1`,
  );
  const { items } = (await response.json()) as { items: Application[] };
  return (
    items[0] ?? { id: "", state: "absent", mode: "", needs: null, form_url: "" }
  );
}

test.beforeAll(async ({ browser }) => {
  person = invented("boardlink");
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
  await setReviewBeforeSend(page.request, false);
});

test("a listing's Apply link leads to the employer's form, sent exactly once", async () => {
  const job = await postingAt(LISTING, ", via a job board");
  await page.goto(`/jobs/${job}`);
  const section = page.getByRole("region", { name: "Apply", exact: true });
  await section.getByRole("button", { name: "Apply", exact: true }).click();
  await expect
    .poll(async () => (await latest(job)).mode, { timeout: 30_000 })
    .toBe("direct");
  // The first fill reads the listing and queues the employer's form; the next fills and sends.
  await expect
    .poll(async () => (await latest(job)).form_url, { timeout: FILL_MS })
    .toBe(`${STACK_FORMS}/greenhouse/northwind-labs/jobs/${JOB}`);
  await expect(
    section.getByRole("status").filter({ hasText: /^Sent\./ }),
  ).toBeVisible({ timeout: FILL_MS + SEND_MS });
  expect(await latest(job)).toMatchObject({
    state: "submitted",
    mode: "direct",
  });
  expect(await sent()).toHaveLength(1);
  // The posting link the person sees is still the board's listing.
  const read = await page.request.get(`/api/jobs/${job}`);
  expect(((await read.json()) as { url: string }).url).toBe(LISTING);
});

test("a script only Apply button: a clear sentence, and I applied records it", async () => {
  const job = await postingAt(`${LISTING}?variant=button`, ", button only");
  await page.goto(`/jobs/${job}`);
  const section = page.getByRole("region", { name: "Apply", exact: true });
  await section.getByRole("button", { name: "Apply", exact: true }).click();
  const stop = section.getByTestId("needs-person");
  await expect(stop).toContainText("Apply from the job board's page", {
    timeout: FILL_MS,
  });
  await expect(stop).toContainText("nothing was sent");
  await expect(stop).not.toContainText("When you are done");
  expect(await latest(job)).toMatchObject({
    state: "needs_person",
    needs: "board_link_missing",
  });
  await expect(
    stop.getByRole("link", { name: /Apply on the site myself/ }),
  ).toHaveAttribute("href", `${LISTING}?variant=button`);
  await stop.getByRole("button", { name: "I applied" }).click();
  await expect
    .poll(
      async () => {
        const read = await page.request.get(`/api/jobs/${job}`);
        return ((await read.json()) as { status: string }).status;
      },
      { timeout: 30_000 },
    )
    .toBe("applied");
  expect((await latest(job)).state).toBe("submitted");
  // The app clicked nothing: the first test's submission is still the only one.
  expect(await sent()).toHaveLength(1);
  expect(errors).toEqual([]);
});
