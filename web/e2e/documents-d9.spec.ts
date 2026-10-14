import { expect, test, type BrowserContext, type Page } from "@playwright/test";

import {
  CSRF,
  caddyUrl,
  keep,
  pdfOf,
  signUp,
  waitForRender,
  invented,
  type Person,
} from "./documents-helpers";
import { clearAttempts, newContext, watchErrors } from "./stack";

/**
 * Documents D9 against the real stack through Caddy, the worker on the fake model backend (the
 * recorded stream `documents_base_journey`, named by the build's `script`) and the real
 * renderer, one fresh account and only invented data: accept the consent, build a base CV from
 * the invented profile, build a second as a copy, make it the default, render it, download a
 * one page PDF whose text holds the invented name, delete the first. The model credential is a
 * made up key the stack's Anthropic stub accepts; nothing reaches Anthropic.
 *
 * Locally, as documents-d6.spec.ts (AGENTS_FAKE_BACKEND=1 on the worker), then:
 *   E2E_CADDY_URL=http://127.0.0.1:8099 E2E_COMPOSE_PROJECT=jobapp-d9 \
 *     pnpm --dir web exec playwright test -c e2e/documents-all.config.ts documents-d9.spec.ts
 */
test.describe.configure({ mode: "serial" });
test.skip(!caddyUrl, "set E2E_CADDY_URL with the Compose stack running");

const EVIDENCE = "documents-d9";
const KEY = `sk-ant-api03-e2egood${"Dn9x".repeat(20)}Rn42`;

type Generation = { status: string; wait_reason: string | null };
type BaseCv = {
  id: string;
  name: string;
  source: string;
  template_slug: string;
  is_default: boolean;
  generation: Generation | null;
};

let person: Person;
let context: BrowserContext;
let page: Page;
let errors: string[];
let firstId: string;
let copyId: string;

async function list(): Promise<BaseCv[]> {
  const response = await page.request.get("/api/documents/base-cvs");
  expect(response.status()).toBe(200);
  return ((await response.json()) as { base_cvs: BaseCv[] }).base_cvs;
}

test.beforeAll(async ({ browser }) => {
  person = invented("d9");
  context = await newContext(browser);
  page = await context.newPage();
  errors = watchErrors(page);
});

test.afterAll(async () => {
  await context.close();
  clearAttempts([person.email]);
});

test("an invented person accepts the consent", async () => {
  await signUp(page, person);
  const saved = await page.request.put("/api/me/model-credential", {
    headers: CSRF,
    data: { kind: "anthropic_api_key", secret: KEY },
  });
  expect(saved.status()).toBe(200);
  const asked = await page.request.get("/api/documents/consent");
  expect(asked.status()).toBe(200);
  const text = (await asked.json()) as {
    version: number;
    accepted: boolean;
    text: string;
  };
  expect(text.accepted).toBe(false);
  expect(text.text).not.toMatch(/[–—]| - /);
  const accepted = await page.request.post("/api/documents/consent", {
    headers: CSRF,
    data: { version: text.version },
  });
  expect(accepted.status()).toBe(200);
  expect(((await accepted.json()) as { accepted: boolean }).accepted).toBe(
    true,
  );
});

test("a base CV is built from the profile and rendered", async () => {
  const built = await page.request.post("/api/documents/base-cvs", {
    headers: CSRF,
    data: { from: "profile", script: "documents_base_journey" },
  });
  expect(built.status()).toBe(202);
  const base = ((await built.json()) as { base_cv: BaseCv }).base_cv;
  expect(base).toEqual(
    expect.objectContaining({ template_slug: "classic", is_default: true }),
  );
  firstId = base.id;
  await expect
    .poll(async () => (await list())[0]?.generation?.status, {
      timeout: 120_000,
      intervals: [500, 1_000, 2_000],
    })
    .toBe("done");
  const rendered = await waitForRender(page.request, firstId, 1);
  expect(rendered.latest?.pages).toBe(1);
});

test("a copy becomes the default and downloads as a one page PDF", async () => {
  const copied = await page.request.post("/api/documents/base-cvs", {
    headers: CSRF,
    data: { from: "copy", copy_of: firstId },
  });
  expect(copied.status()).toBe(201);
  copyId = ((await copied.json()) as { base_cv: BaseCv }).base_cv.id;
  const made = await page.request.patch(`/api/documents/base-cvs/${copyId}`, {
    headers: CSRF,
    data: { default: true },
  });
  expect(made.status()).toBe(200);
  expect(((await made.json()) as BaseCv).is_default).toBe(true);

  const rendered = await waitForRender(page.request, copyId, 1);
  const pdf = await pdfOf(page.request, copyId, rendered.latest?.number ?? 0);
  expect(pdf.pages).toBe(1);
  expect(pdf.text).toContain(person.name);
  expect(pdf.text).toContain(person.firstEmployer);
  keep(EVIDENCE, "copy.pdf", pdf.bytes);
});

test("deleting the first leaves the copy, the default", async () => {
  const deleted = await page.request.delete(
    `/api/documents/base-cvs/${firstId}`,
    {
      headers: CSRF,
    },
  );
  expect(deleted.status()).toBe(204);
  const left = await list();
  expect(left.map((row) => [row.id, row.is_default])).toEqual([[copyId, true]]);
  expect(errors).toEqual([]);
});
