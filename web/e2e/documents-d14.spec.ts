import { expect, test, type BrowserContext, type Page } from "@playwright/test";

import {
  CSRF,
  addPosting,
  caddyUrl,
  invented,
  jobStatus,
  keep,
  pdfOf,
  saveDraft,
  seedDocuments,
  signUp,
  waitForRender,
  type Person,
} from "./documents-helpers";
import { clearAttempts, newContext, watchErrors } from "./stack";

/**
 * Documents D14 against the real stack through Caddy, the worker and the real renderer, no
 * mocks, one fresh account and only invented data: a CV rendered through the api, the posting
 * set to `applied` on the job page, and the version is then frozen with the reason `applied`
 * (read through the api); a changed draft is saved and rendered, a new version appears and the
 * sent one is unchanged, its PDF byte for byte (hash compared); marking the sent version as sent
 * again changes nothing.
 *
 * Locally, on a scratch Compose project (its own volumes, torn down afterwards):
 *   HTTP_PORT=8099 docker compose -p jobapp-d14 -f compose.yml -f compose.mac.yml up -d --wait
 *   docker compose -p jobapp-d14 -f compose.yml exec -T api alembic upgrade head
 *   E2E_CADDY_URL=http://127.0.0.1:8099 E2E_COMPOSE_PROJECT=jobapp-d14 \
 *     pnpm --dir web exec playwright test -c e2e/documents-all.config.ts documents-d14.spec.ts
 *   docker compose -p jobapp-d14 -f compose.yml down -v
 */
test.describe.configure({ mode: "serial" });
test.skip(!caddyUrl, "set E2E_CADDY_URL with the Compose stack running");

type Version = {
  number: number;
  frozen_at: string | null;
  frozen_reason: string | null;
  pages: number | null;
};

let person: Person;
let context: BrowserContext;
let page: Page;
let errors: string[];
let postingId: string;
let cvId: string;
let sentPdf: Buffer;
let sentFrozenAt: string | null;

async function versions(): Promise<Version[]> {
  const response = await page.request.get(`/api/documents/${cvId}/versions`);
  expect(response.status()).toBe(200);
  return (await response.json()) as Version[];
}

test.beforeAll(async ({ browser }) => {
  person = invented("d14");
  context = await newContext(browser);
  page = await context.newPage();
  errors = watchErrors(page);
});

test.afterAll(async () => {
  await context.close();
  clearAttempts([person.email]);
});

test("a CV is rendered through the api and nothing is frozen yet", async () => {
  await signUp(page, person);
  postingId = await addPosting(page.request, person);
  cvId = seedDocuments(person, postingId).cv;
  await saveDraft(page.request, cvId, person.cvDraft);
  const rendered = await waitForRender(page.request, cvId, 1);
  expect(rendered.latest?.number).toBe(1);
  const listed = await versions();
  expect(listed.map((v) => v.frozen_reason)).toEqual([null]);
  expect(await jobStatus(page.request, postingId)).toBe("cv_ready");
});

test("setting the posting to applied on the job page freezes the version", async () => {
  await page.goto(`/jobs/${postingId}`);
  await page.getByLabel("Status", { exact: true }).selectOption("applied");
  await page.getByRole("button", { name: "Mark as applied" }).click();
  await expect(page.getByText("Status set to Applied.")).toBeVisible();
  expect(await jobStatus(page.request, postingId)).toBe("applied");

  const first = (await versions()).find((v) => v.number === 1);
  expect(first?.frozen_reason).toBe("applied");
  expect(first?.frozen_at).not.toBeNull();
  sentFrozenAt = first?.frozen_at ?? null;
  const pdf = await pdfOf(page.request, cvId, 1);
  expect(pdf.pages).toBe(1);
  sentPdf = pdf.bytes;
  keep("documents-d14", "sent.pdf", pdf.bytes);
});

test("a changed draft makes a new version and the sent one stays as it was", async () => {
  const changed = {
    ...person.cvDraft,
    headline: "After the send",
  };
  await saveDraft(page.request, cvId, changed);
  const rendered = await waitForRender(page.request, cvId, 2);
  expect(rendered.latest?.number).toBe(2);

  const listed = await versions();
  const byNumber = new Map(listed.map((v) => [v.number, v]));
  expect(byNumber.get(2)?.frozen_reason).toBeNull();
  expect(byNumber.get(1)?.frozen_reason).toBe("applied");
  expect(byNumber.get(1)?.frozen_at).toBe(sentFrozenAt);

  const sent = await pdfOf(page.request, cvId, 1);
  expect(sent.bytes.equals(sentPdf)).toBe(true);
  const newer = await pdfOf(page.request, cvId, 2);
  expect(newer.bytes.equals(sentPdf)).toBe(false);

  // Approving the sent version is refused, marking it sent again changes nothing.
  const approve = await page.request.post(
    `/api/documents/${cvId}/versions/1/approve`,
    { headers: CSRF },
  );
  expect(approve.status()).toBe(409);
  const again = await page.request.post(
    `/api/documents/${cvId}/versions/1/mark-sent`,
    { headers: CSRF },
  );
  expect(again.status()).toBe(200);
  expect(((await again.json()) as Version).frozen_reason).toBe("applied");
  expect((await pdfOf(page.request, cvId, 1)).bytes.equals(sentPdf)).toBe(true);
  expect(errors).toEqual([]);
});
