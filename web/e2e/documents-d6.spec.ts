import { expect, test, type BrowserContext, type Page } from "@playwright/test";

import {
  addPosting,
  caddyUrl,
  jobStatus,
  keep,
  patchDocument,
  pdfOf,
  saveDraft,
  seedDocuments,
  signUp,
  waitForRender,
  invented,
  type Person,
} from "./documents-helpers";
import { clearAttempts, newContext, watchErrors } from "./stack";

/**
 * Documents D6 against the real stack through Caddy, the worker and the real renderer, no mocks,
 * one fresh account and only invented data: a profile, a base CV and a posting; a hand written
 * CV draft saved through the api is rendered to a one page PDF whose text holds the invented
 * name and the first employer's organisation; switching to the `sidebar` template makes a new
 * version with a new one page PDF, the first one unchanged; the posting is then `cv_ready`.
 * The first page of each version is kept as evidence (tasks/evidence/documents-d6/).
 *
 * Locally, on a scratch Compose project (its own volumes, torn down afterwards):
 *   HTTP_PORT=8099 docker compose -p jobapp-d6 -f compose.yml -f compose.mac.yml up -d --wait
 *   docker compose -p jobapp-d6 -f compose.yml exec -T api alembic upgrade head
 *   E2E_CADDY_URL=http://127.0.0.1:8099 E2E_COMPOSE_PROJECT=jobapp-d6 \
 *     pnpm --dir web exec playwright test -c e2e/documents-all.config.ts documents-d6.spec.ts
 *   docker compose -p jobapp-d6 -f compose.yml down -v
 */
test.describe.configure({ mode: "serial" });
test.skip(!caddyUrl, "set E2E_CADDY_URL with the Compose stack running");

const EVIDENCE = "documents-d6";

let person: Person;
let context: BrowserContext;
let page: Page;
let errors: string[];
let postingId: string;
let cvId: string;
let firstPdf: Buffer;

test.beforeAll(async ({ browser }) => {
  person = invented("d6");
  context = await newContext(browser);
  page = await context.newPage();
  errors = watchErrors(page);
});

test.afterAll(async () => {
  await context.close();
  clearAttempts([person.email]);
});

test("an invented person with a profile, a base CV and a posting", async () => {
  await signUp(page, person);
  postingId = await addPosting(page.request, person);
  expect(await jobStatus(page.request, postingId)).toBe("found");
  const seeded = seedDocuments(person, postingId);
  cvId = seeded.cv;

  const listed = await page.request.get(`/api/jobs/${postingId}/documents`);
  expect(listed.status()).toBe(200);
  const { documents } = (await listed.json()) as {
    documents: Array<{ id: string; kind: string; latest: unknown }>;
  };
  expect(documents).toEqual([
    expect.objectContaining({ id: cvId, kind: "cv", latest: null }),
  ]);
});

test("a hand written draft renders to a one page PDF with the person's facts", async () => {
  const saved = await saveDraft(page.request, cvId, person.cvDraft);
  expect(saved.render.state).toBe("queued");
  expect(saved.render.revision).toBe(saved.draft_revision);
  expect(saved.template_slug).toBe("classic");

  const rendered = await waitForRender(page.request, cvId, 1);
  const latest = rendered.latest;
  expect(latest).toEqual(
    expect.objectContaining({
      template_slug: "classic",
      pages: 1,
      has_pdf: true,
    }),
  );
  const pdf = await pdfOf(page.request, cvId, latest?.number ?? 0);
  expect(pdf.pages).toBe(1);
  expect(pdf.text).toContain(person.name);
  expect(pdf.text).toContain(person.firstEmployer);
  expect(pdf.filename).toBe("Morgan_Exampleton_Brindle_Example_Labs_CV.pdf");
  firstPdf = pdf.bytes;
  keep(EVIDENCE, "classic.pdf", pdf.bytes);
  const image = await page.request.get(
    `/api/documents/${cvId}/versions/${latest?.number ?? 0}/page1.png`,
  );
  expect(image.status()).toBe(200);
  keep(EVIDENCE, "classic-page1.png", Buffer.from(await image.body()));
});

test("switching to sidebar makes a new one page version, the first unchanged", async () => {
  const before =
    (await waitForRender(page.request, cvId, 1)).latest?.number ?? 0;
  const patched = await patchDocument(page.request, cvId, {
    template_slug: "sidebar",
  });
  expect(patched.template_slug).toBe("sidebar");

  const rendered = await waitForRender(page.request, cvId, before + 1);
  const latest = rendered.latest;
  expect(latest).toEqual(
    expect.objectContaining({
      template_slug: "sidebar",
      pages: 1,
      has_pdf: true,
    }),
  );
  const pdf = await pdfOf(page.request, cvId, latest?.number ?? 0);
  expect(pdf.pages).toBe(1);
  expect(pdf.text).toContain(person.name);
  expect(pdf.text).toContain(person.firstEmployer);
  expect(pdf.bytes.equals(firstPdf)).toBe(false);
  keep(EVIDENCE, "sidebar.pdf", pdf.bytes);
  const image = await page.request.get(
    `/api/documents/${cvId}/versions/${latest?.number ?? 0}/page1.png`,
  );
  expect(image.status()).toBe(200);
  keep(EVIDENCE, "sidebar-page1.png", Buffer.from(await image.body()));

  const first = await pdfOf(page.request, cvId, before);
  expect(first.bytes.equals(firstPdf)).toBe(true);
});

test("the posting is cv_ready", async () => {
  expect(await jobStatus(page.request, postingId)).toBe("cv_ready");
  expect(errors).toEqual([]);
});
