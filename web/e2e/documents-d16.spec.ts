import { expect, test, type BrowserContext, type Page } from "@playwright/test";

import {
  addPosting,
  caddyUrl,
  invented,
  pdfOf,
  readDocument,
  saveDraft,
  seedDocuments,
  signUp,
  waitForRender,
  type Person,
} from "./documents-helpers";
import { clearAttempts, makeShots, newContext, watchErrors } from "./stack";

/**
 * Documents D16 against the real stack through Caddy, the worker and the real renderer, no
 * mocks, one fresh account and only invented data: a posting's CV rendered once through the api
 * (as D12's journey ends), then, through the page, its summary changed in the Edit view with the
 * template's counter, saved by itself and drawn as version 2; then version 1 restored into the
 * draft and approved from the version history of the See view. The Edit view and the history
 * are checked on a phone and a desktop viewport for horizontal overflow (`makeShots`).
 *
 * Locally, on a scratch Compose project (its own volumes, torn down afterwards):
 *   HTTP_PORT=8099 docker compose -p jobapp-d16 -f compose.yml -f compose.mac.yml up -d --wait
 *   docker compose -p jobapp-d16 -f compose.yml exec -T api alembic upgrade head
 *   E2E_CADDY_URL=http://127.0.0.1:8099 E2E_COMPOSE_PROJECT=jobapp-d16 \
 *     pnpm --dir web exec playwright test -c e2e/documents-all.config.ts documents-d16.spec.ts
 *   docker compose -p jobapp-d16 -f compose.yml down -v
 */
test.describe.configure({ mode: "serial" });
test.skip(!caddyUrl, "set E2E_CADDY_URL with the Compose stack running");

const EVIDENCE = "documents-d16";
const SEPARATOR = /[–—]| - /;
const SUMMARY =
  "Platform engineer with about 3.5 years of professional engineering. I keep the deployments of the billing team at Quillmere Example Works calm.";

type Version = { number: number; approved_at: string | null };

let person: Person;
let context: BrowserContext;
let page: Page;
let errors: string[];
let postingId: string;
let cvId: string;
let firstSummary: string;

async function versions(): Promise<Version[]> {
  const response = await page.request.get(`/api/documents/${cvId}/versions`);
  expect(response.status()).toBe(200);
  return (await response.json()) as Version[];
}

test.beforeAll(async ({ browser }) => {
  person = invented("d16");
  context = await newContext(browser);
  page = await context.newPage();
  errors = watchErrors(page);
});

test.afterAll(async () => {
  await context.close();
  clearAttempts([person.email]);
});

test("a posting's CV is rendered once", async () => {
  await signUp(page, person);
  postingId = await addPosting(page.request, person);
  cvId = seedDocuments(person, postingId).cv;
  await saveDraft(page.request, cvId, person.cvDraft);
  const rendered = await waitForRender(page.request, cvId, 1);
  expect(rendered.latest?.number).toBe(1);
  firstSummary = String(rendered.draft.summary);
});

test("the summary changed in the Edit view is counted, saved by itself and drawn", async () => {
  await page.goto(`/jobs/${postingId}/documents/cv`);
  await page.getByRole("tab", { name: "Edit" }).click();
  const summary = page.getByLabel("Summary", { exact: true });
  await expect(summary).toHaveValue(firstSummary);
  await expect(summary).toHaveAccessibleDescription(
    new RegExp(`^${String(firstSummary.length)} of about \\d+ characters`),
  );
  await expect(
    page.getByRole("region", {
      name: "From your profile, printed as they are",
    }),
  ).toContainText(person.name);

  await summary.fill(SUMMARY);
  await expect(summary).toHaveAccessibleDescription(
    new RegExp(`^${String(SUMMARY.length)} of about \\d+ characters`),
  );
  await expect(page.getByText(/^Saved/)).toBeVisible({ timeout: 15_000 });
  const drawn = await waitForRender(page.request, cvId, 2);
  expect(drawn.latest?.number).toBe(2);
  expect(drawn.draft.summary).toBe(SUMMARY);
  const pdf = await pdfOf(page.request, cvId, 2);
  expect(pdf.text.replace(/\s+/g, " ")).toContain("billing team");
  expect(await page.getByTestId("edit-panel").textContent()).not.toMatch(
    SEPARATOR,
  );
  await makeShots(EVIDENCE, "d16")(page, "edit");
});

test("version 1 is restored into the draft and approved from the history", async () => {
  await page.getByRole("tab", { name: "See" }).click();
  const history = page.getByRole("region", { name: "Versions" });
  await history.getByRole("button", { name: "Restore version 1" }).click();
  const dialog = page.getByRole("alertdialog", {
    name: "Restore version 1 into the draft?",
  });
  await dialog.getByRole("button", { name: "Restore" }).click();
  await expect
    .poll(async () => (await readDocument(page.request, cvId)).draft.summary, {
      timeout: 15_000,
    })
    .toBe(firstSummary);
  await waitForRender(page.request, cvId, 2);

  await history.getByRole("button", { name: "Approve version 1" }).click();
  await expect
    .poll(
      async () =>
        (await versions()).find((one) => one.number === 1)?.approved_at ?? null,
      { timeout: 15_000 },
    )
    .not.toBeNull();
  await expect(
    history.getByRole("button", { name: "Approve version 1" }),
  ).toHaveCount(0);
  await expect(history).toContainText("approved");
  await makeShots(EVIDENCE, "d16")(page, "history");

  await page.getByRole("tab", { name: "Edit" }).click();
  await expect(page.getByLabel("Summary", { exact: true })).toHaveValue(
    firstSummary,
  );
  expect(errors).toEqual([]);
});
