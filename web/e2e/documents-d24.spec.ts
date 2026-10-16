import fs from "node:fs";
import path from "node:path";

import { expect, test, type BrowserContext, type Page } from "@playwright/test";

import {
  CSRF,
  caddyUrl,
  invented,
  keep,
  pdfOf,
  readDocument,
  signUp,
  waitForRender,
  type Person,
} from "./documents-helpers";
import { makePdf } from "./ingestion-pdf";
import { clearAttempts, newContext, root, watchErrors } from "./stack";

/**
 * Documents D24 against the real stack through Caddy, driven through the page: an account with
 * an invented profile and a made up model credential (the stack's Anthropic stub accepts it),
 * the worker on the fake model backend and the real parser and renderer. An invented CV, a PDF
 * written by ./ingestion-pdf.ts from api/tests/documents/fixtures/upload_morgan.txt, is dropped
 * on the upload panel of the CV tab and goes through ingestion's upload; once its text is read
 * the consent is asked, then the conversion runs on the default recorded stream
 * `documents_base_upload_valid` (the page names no script; the api test
 * test_the_journeys_person_converts_the_journeys_cv_on_the_default_stream plays the same stream
 * for the same person). The new base CV holds the invented employers in its fields (read
 * through the api), is the default, renders and downloads as a one page PDF. "Complete my
 * profile from this CV" opens ingestion's run dialog and no run starts.
 *
 * Locally, as documents-d6.spec.ts (AGENTS_FAKE_BACKEND=1 on the worker), then:
 *   E2E_CADDY_URL=http://127.0.0.1:8099 E2E_COMPOSE_PROJECT=jobapp-d24 \
 *     pnpm --dir web exec playwright test -c e2e/documents-all.config.ts documents-d24.spec.ts
 */
test.describe.configure({ mode: "serial" });
test.skip(!caddyUrl, "set E2E_CADDY_URL with the Compose stack running");

const EVIDENCE = "documents-d24";
const KEY = `sk-ant-api03-e2egood${"Dn9x".repeat(20)}Rn42`;
const FILE = "morgan-exampleton-cv.pdf";
const LINES = fs
  .readFileSync(
    path.join(root, "api/tests/documents/fixtures/upload_morgan.txt"),
    "utf8",
  )
  .trimEnd()
  .split("\n");

type BaseCv = {
  id: string;
  name: string;
  source: string;
  is_default: boolean;
  generation: { status: string; error: string | null } | null;
};
type Converted = {
  fixed: { employers: Array<{ organisation: string; title: string }> };
  experience: Array<{ employer_key: string; bullets: string[] }>;
};

let person: Person;
let context: BrowserContext;
let page: Page;
let errors: string[];
let uploadedId: string;

async function list(): Promise<BaseCv[]> {
  const response = await page.request.get("/api/documents/base-cvs");
  expect(response.status()).toBe(200);
  return ((await response.json()) as { base_cvs: BaseCv[] }).base_cvs;
}

async function uploaded(): Promise<BaseCv | undefined> {
  return (await list()).find((one) => one.source === "uploaded");
}

async function runs(): Promise<unknown[]> {
  const response = await page.request.get("/api/ingestion/runs");
  expect(response.status()).toBe(200);
  return ((await response.json()) as { runs: unknown[] }).runs;
}

test.beforeAll(async ({ browser }) => {
  person = invented("d24");
  context = await newContext(browser);
  page = await context.newPage();
  errors = watchErrors(page);
});

test.afterAll(async () => {
  await context.close();
  clearAttempts([person.email]);
});

test("an invented CV dropped on the CV tab is read, then the consent is asked", async () => {
  await signUp(page, person);
  const saved = await page.request.put("/api/me/model-credential", {
    headers: CSRF,
    data: { kind: "anthropic_api_key", secret: KEY },
  });
  expect(saved.status()).toBe(200);

  await page.goto("/profile");
  await page.getByRole("tab", { name: "CV" }).click();
  await expect(page).toHaveURL(/\/profile\/cv$/);
  const panel = page.getByTestId("upload-panel");
  await expect(
    panel.getByRole("heading", { name: "Upload your CV" }),
  ).toBeVisible();

  // Dropped as ingestion-g1.spec.ts drops a file: the events a browser fires for a drag.
  await panel.getByTestId("upload-drop").evaluate(
    (zone, { bytes, name }) => {
      const transfer = new DataTransfer();
      transfer.items.add(
        new File([new Uint8Array(bytes)], name, { type: "application/pdf" }),
      );
      for (const type of ["dragover", "drop"])
        zone.dispatchEvent(
          new DragEvent(type, {
            dataTransfer: transfer,
            bubbles: true,
            cancelable: true,
          }),
        );
    },
    { bytes: [...makePdf([{ lines: LINES }])], name: FILE },
  );
  await expect(panel.getByText("Reading your CV")).toBeVisible();

  const dialog = page.getByRole("dialog", {
    name: "What is sent to build your CV",
  });
  await expect(dialog).toBeVisible({ timeout: 60_000 });
  expect(await uploaded()).toBeUndefined(); // nothing is converted before the agreement
  keep(EVIDENCE, "consent.png", await page.screenshot());
  await dialog.getByRole("checkbox").check();
  await dialog.getByRole("button", { name: "Agree and build my CV" }).click();
  await expect(dialog).toBeHidden();
});

test("the converted CV is the default, with the invented employers in its fields", async () => {
  await expect
    .poll(async () => (await uploaded())?.generation?.status, {
      timeout: 120_000,
      intervals: [500, 1_000, 2_000],
    })
    .toBe("done");
  const cv = await uploaded();
  uploadedId = cv?.id ?? "";
  expect(cv).toEqual(
    expect.objectContaining({ name: "morgan-exampleton-cv", is_default: true }),
  );

  const draft = (await readDocument(page.request, uploadedId))
    .draft as Converted;
  expect(draft.fixed.employers).toEqual([
    expect.objectContaining({
      organisation: person.firstEmployer,
      title: "Platform Engineer",
    }),
    expect.objectContaining({
      organisation: "Tarnwick Sample Studio",
      title: "Software Developer",
    }),
  ]);
  expect(draft.experience.flatMap((entry) => entry.bullets)).toContain(
    "Moved 30 services to Kubernetes with Terraform.",
  );

  const result = page.getByTestId("upload-result");
  await expect(result).toContainText(
    "morgan-exampleton-cv is ready to edit. It is your default CV.",
  );
  await expect(
    result.getByRole("button", { name: "Make this my default CV" }),
  ).toHaveCount(0);
  keep(EVIDENCE, "converted.png", await page.screenshot({ fullPage: true }));
});

test("Complete my profile from this CV opens ingestion's run dialog, and no run starts", async () => {
  await page
    .getByTestId("upload-result")
    .getByRole("button", { name: "Complete my profile from this CV" })
    .click();
  const dialog = page.getByRole("dialog", { name: "Before the run starts" });
  await expect(dialog).toBeVisible();
  await expect(
    dialog.getByRole("button", { name: /Start the run/ }),
  ).toBeVisible();
  keep(EVIDENCE, "complete-profile.png", await page.screenshot());
  expect(await runs()).toEqual([]);

  await dialog.getByRole("button", { name: "Not now" }).click();
  await expect(dialog).toBeHidden();
  expect(await runs()).toEqual([]);
});

test("the converted CV renders and downloads as a one page PDF", async () => {
  const rendered = await waitForRender(page.request, uploadedId, 1);
  await page
    .getByTestId("upload-result")
    .getByRole("link", { name: "Open it" })
    .click();
  await expect(page).toHaveURL(new RegExp(`/profile/cv/${uploadedId}$`));
  await expect(
    page.getByRole("img", { name: /^Page 1 of version/ }),
  ).toBeVisible();
  await expect(page.getByTestId("fit")).toHaveText("One page");

  const [download] = await Promise.all([
    page.waitForEvent("download"),
    page.getByRole("link", { name: "Download PDF" }).click(),
  ]);
  expect(download.suggestedFilename()).toMatch(/\.pdf$/);
  const pdf = await pdfOf(
    page.request,
    uploadedId,
    rendered.latest?.number ?? 0,
  );
  expect(pdf.pages).toBe(1);
  expect(pdf.text).toContain(person.name);
  expect(pdf.text).toContain(person.firstEmployer);
  keep(EVIDENCE, "converted.pdf", pdf.bytes);
  expect(errors).toEqual([]);
});
