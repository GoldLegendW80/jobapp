import { expect, test, type BrowserContext, type Page } from "@playwright/test";

import {
  CSRF,
  addPosting,
  caddyUrl,
  invented,
  jobStatus,
  keep,
  pdfOf,
  signUp,
  waitForRender,
  type Person,
} from "./documents-helpers";
import { clearAttempts, newContext, watchErrors } from "./stack";

/**
 * Documents D12 against the real stack through Caddy, driven through the page: an invented
 * account with a made up model credential, the consent and a base CV, the writers on the fake
 * backend and the real renderer. The page cannot name a recorded stream, so the write request it
 * sends is given the `journey` streams (`documents_cv_journey`, `documents_letter_journey`) on
 * its way out: the default ones are written for another profile (their employer keys are no
 * fixed employer of this one) and would end without a result. Add a posting with fit 80, open
 * it, write its CV and letter with one click, see both page 1 previews and "Based on" naming the
 * base CV; the posting is `cv_ready` and its badge says so without a reload. Open the CV, download
 * the PDF. Write the CV again from a copy of the base CV: the letter follows it. A posting with
 * fit 40 says it is under the threshold and "Write now" writes its CV.
 *
 * Locally, as documents-d6.spec.ts (AGENTS_FAKE_BACKEND=1 on the worker), then:
 *   E2E_CADDY_URL=http://127.0.0.1:8099 E2E_COMPOSE_PROJECT=jobapp-d12 \
 *     pnpm --dir web exec playwright test -c e2e/documents-all.config.ts documents-d12.spec.ts
 */
test.describe.configure({ mode: "serial" });
test.skip(!caddyUrl, "set E2E_CADDY_URL with the Compose stack running");

const EVIDENCE = "documents-d12";
const KEY = `sk-ant-api03-e2egood${"Dn9x".repeat(20)}Rn42`;

type PostingDocument = {
  id: string;
  kind: "cv" | "letter";
  latest: { number: number } | null;
  generation: { status: string } | null;
};

let person: Person;
let context: BrowserContext;
let page: Page;
let errors: string[];
let high: string;
let low: string;
let baseName: string;
let baseId: string;
/** The bodies the page sent to the write route, in order. */
const written: { kinds: string[]; base_document_id?: string }[] = [];

async function documentsOf(job: string): Promise<PostingDocument[]> {
  const response = await page.request.get(`/api/jobs/${job}/documents`);
  expect(response.status()).toBe(200);
  return ((await response.json()) as { documents: PostingDocument[] })
    .documents;
}

/** Set the hand set fit of a posting, as the job page's own control does. */
async function setFit(job: string, fit: number) {
  const read = await page.request.get(`/api/jobs/${job}`);
  const { updated_at } = (await read.json()) as { updated_at: string };
  const patched = await page.request.patch(`/api/jobs/${job}`, {
    headers: CSRF,
    data: { fit, updated_at },
  });
  expect(patched.status()).toBe(200);
}

test.beforeAll(async ({ browser }) => {
  person = invented("d12");
  context = await newContext(browser);
  page = await context.newPage();
  errors = watchErrors(page);
  await page.route("**/api/jobs/*/documents/write", async (route) => {
    const request = route.request();
    if (request.method() !== "POST") {
      await route.continue();
      return;
    }
    const body = request.postDataJSON() as (typeof written)[number];
    written.push(body);
    await route.continue({
      postData: JSON.stringify({ ...body, script: "journey" }),
    });
  });
});

test.afterAll(async () => {
  await context.close();
  clearAttempts([person.email]);
});

test("an invented person has the consent, a base CV and a posting at fit 80", async () => {
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
  const base = (
    (await built.json()) as { base_cv: { id: string; name: string } }
  ).base_cv;
  baseName = base.name;
  baseId = base.id;
  await waitForRender(page.request, base.id, 1);

  high = await addPosting(page.request, person);
  await setFit(high, 80);
});

test("both cards are written with one click and show a one page preview", async () => {
  await page.goto(`/jobs/${high}`);
  const section = page.getByRole("region", { name: "Documents" });
  await expect(section.getByTestId("posting-cv")).toContainText(
    "Not written yet",
  );
  await expect(section.getByTestId("posting-cv")).toContainText(
    `Based on: ${baseName}`,
  );
  await section.getByRole("button", { name: "Write both now" }).click();

  await expect
    .poll(
      async () =>
        (await documentsOf(high)).map((one) => one.latest?.number ?? 0),
      { timeout: 150_000, intervals: [500, 1_000, 2_000] },
    )
    .toEqual([1, 1]);
  const [cv] = (await documentsOf(high)).filter((one) => one.kind === "cv");
  await waitForRender(page.request, cv?.id ?? "", 1);

  // No reload from here on: the page reads the letter that follows the CV, and the posting.
  for (const [card, noun] of [
    ["posting-cv", "CV"],
    ["posting-letter", "letter"],
  ] as const) {
    const one = section.getByTestId(card);
    await expect(one).toContainText("Ready, on one page", { timeout: 60_000 });
    await expect(
      one.getByRole("img", { name: `Page 1 of the ${noun}, version 1` }),
    ).toBeVisible();
    await expect(
      one.getByRole("link", { name: `Open the ${noun}`, exact: true }),
    ).toBeVisible();
    await expect(
      one.getByRole("link", {
        name: `Download PDF of the ${noun}`,
        exact: true,
      }),
    ).toBeVisible();
  }
  await expect
    .poll(() => jobStatus(page.request, high), { timeout: 30_000 })
    .toBe("cv_ready");
  await expect(page.locator("[data-status]")).toHaveAttribute(
    "data-status",
    "cv_ready",
    { timeout: 30_000 },
  );
  await expect(page.locator("[data-status]")).toHaveText("CV ready");
  keep(EVIDENCE, "cards.png", await page.screenshot({ fullPage: true }));
});

test("the CV opens in the document view and downloads as a one page PDF", async () => {
  await page.goto(`/jobs/${high}`);
  await page
    .getByTestId("posting-cv")
    .getByRole("link", { name: "Open the CV", exact: true })
    .click();
  await expect(page).toHaveURL(new RegExp(`/jobs/${high}/documents/cv$`));
  await expect(page.getByTestId("fit")).toHaveText("One page");
  const [download] = await Promise.all([
    page.waitForEvent("download"),
    page.getByRole("link", { name: "Download PDF" }).click(),
  ]);
  expect(download.suggestedFilename()).toMatch(/\.pdf$/);
  const [cv] = (await documentsOf(high)).filter((one) => one.kind === "cv");
  const pdf = await pdfOf(page.request, cv?.id ?? "", cv?.latest?.number ?? 0);
  expect(pdf.pages).toBe(1);
  expect(pdf.text).toContain(person.name);
  keep(EVIDENCE, "cv.pdf", pdf.bytes);
});

test("the CV written again from another base CV takes the letter along", async () => {
  const copied = await page.request.post("/api/documents/base-cvs", {
    headers: CSRF,
    data: { from: "copy", copy_of: baseId, name: "Copy for D12" },
  });
  expect(copied.status()).toBe(201);
  const copy = ((await copied.json()) as { base_cv: { id: string } }).base_cv;

  await page.goto(`/jobs/${high}`);
  const cv = page.getByTestId("posting-cv");
  await cv.getByRole("combobox").selectOption(copy.id);
  await expect(cv).toContainText(
    "The letter is then written again from the new CV.",
  );
  await cv.getByRole("button", { name: "Write it again from this CV" }).click();
  await expect
    .poll(
      async () =>
        (await documentsOf(high)).map((one) => one.latest?.number ?? 0),
      { timeout: 150_000, intervals: [500, 1_000, 2_000] },
    )
    .toEqual([2, 2]);
  expect(written.at(-1)).toEqual({
    kinds: ["cv", "letter"],
    base_document_id: copy.id,
  });
  for (const card of ["posting-cv", "posting-letter"]) {
    await expect(page.getByTestId(card)).toContainText("Ready, on one page", {
      timeout: 60_000,
    });
  }
  await expect(cv).toContainText("Based on: Copy for D12");
});

test("a posting under the threshold says so, and Write now writes its CV", async () => {
  low = await addPosting(page.request, {
    ...person,
    posting: { ...person.posting, title: "Support engineer" },
  });
  await setFit(low, 40);
  await page.goto(`/jobs/${low}`);
  const cv = page.getByTestId("posting-cv");
  await expect(cv).toContainText("Fit 40 is under your threshold of 70");
  await cv.getByRole("button", { name: "Write now" }).click();
  await expect
    .poll(
      async () =>
        (await documentsOf(low)).find((one) => one.kind === "cv")?.latest
          ?.number ?? 0,
      { timeout: 150_000, intervals: [500, 1_000, 2_000] },
    )
    .toBe(1);
  await expect(cv).toContainText("Ready, on one page", { timeout: 60_000 });
  expect(errors).toEqual([]);
});
