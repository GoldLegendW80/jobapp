import { expect, test, type BrowserContext, type Page } from "@playwright/test";

import {
  CSRF,
  caddyUrl,
  invented,
  keep,
  pdfOf,
  signUp,
  waitForRender,
  type Person,
} from "./documents-helpers";
import { clearAttempts, newContext, watchErrors } from "./stack";

/**
 * Documents D11 against the real stack through Caddy, driven through the page: an account with
 * an invented profile and a made up model credential (the stack's Anthropic stub accepts it),
 * the worker on the fake model backend (the build uses its default recorded stream) and the real
 * renderer. Open the profile, accept the consent, build a CV from the profile and see its page,
 * build a second as a copy, make it the default, download the PDF, reload and see the same list.
 *
 * Locally, as documents-d6.spec.ts (AGENTS_FAKE_BACKEND=1 on the worker), then:
 *   E2E_CADDY_URL=http://127.0.0.1:8099 E2E_COMPOSE_PROJECT=jobapp-d11 \
 *     pnpm --dir web exec playwright test -c e2e/documents-all.config.ts documents-d11.spec.ts
 */
test.describe.configure({ mode: "serial" });
test.skip(!caddyUrl, "set E2E_CADDY_URL with the Compose stack running");

const EVIDENCE = "documents-d11";
const KEY = `sk-ant-api03-e2egood${"Dn9x".repeat(20)}Rn42`;

type BaseCv = {
  id: string;
  name: string;
  is_default: boolean;
  generation: { status: string } | null;
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
  person = invented("d11");
  context = await newContext(browser);
  page = await context.newPage();
  errors = watchErrors(page);
});

test.afterAll(async () => {
  await context.close();
  clearAttempts([person.email]);
});

test("the CV tab opens first and asks what is sent before anything is built", async () => {
  await signUp(page, person);
  const saved = await page.request.put("/api/me/model-credential", {
    headers: CSRF,
    data: { kind: "anthropic_api_key", secret: KEY },
  });
  expect(saved.status()).toBe(200);

  await page.goto("/profile");
  await page.getByRole("tab", { name: "CV" }).click();
  await expect(page).toHaveURL(/\/profile\/cv$/);
  await expect(page.getByText("You have no CV yet")).toBeVisible();
  await expect(
    page.getByText("Confirm what is sent to build your CV."),
  ).toBeVisible();

  await page.getByRole("button", { name: "Build it from my profile" }).click();
  const dialog = page.getByRole("dialog", {
    name: "What is sent to build your CV",
  });
  await expect(dialog.getByTestId("consent-text")).toBeVisible();
  await expect(dialog.getByTestId("consent-estimates")).toContainText(
    "One posting:",
  );
  expect((await list()).length).toBe(0);
  await dialog.getByRole("checkbox").check();
  await dialog.getByRole("button", { name: "Agree and build my CV" }).click();
  await expect(page.getByText(/is being built/)).toBeVisible();
  keep(EVIDENCE, "building.png", await page.screenshot());
});

test("the built CV shows its one page, and a reload shows the same list", async () => {
  await expect
    .poll(async () => (await list())[0]?.generation?.status, {
      timeout: 120_000,
      intervals: [500, 1_000, 2_000],
    })
    .toBe("done");
  const [first] = await list();
  firstId = first?.id ?? "";
  expect(first?.is_default).toBe(true);
  await waitForRender(page.request, firstId, 1);

  await page.reload();
  await page.getByRole("link", { name: first?.name ?? "" }).click();
  await expect(page).toHaveURL(new RegExp(`/profile/cv/${firstId}$`));
  await expect(
    page.getByRole("img", { name: /^Page 1 of version/ }),
  ).toBeVisible();
  await expect(page.getByTestId("fit")).toHaveText("One page");
  keep(EVIDENCE, "first-cv.png", await page.screenshot({ fullPage: true }));
});

test("a copy becomes the default and downloads as a one page PDF", async () => {
  await page.goto("/profile/cv");
  await page.getByRole("button", { name: "New CV" }).click();
  const dialog = page.getByRole("dialog", { name: "New CV" });
  await dialog.getByRole("radio", { name: "A copy of another CV" }).check();
  await dialog.getByLabel("Name (optional)").fill("Copy for Paris");
  await dialog.getByRole("button", { name: "Make a copy" }).click();
  await expect(
    page.getByRole("link", { name: "Copy for Paris" }),
  ).toBeVisible();
  copyId =
    (await list()).find((one) => one.name === "Copy for Paris")?.id ?? "";
  expect(copyId).not.toBe("");

  await page
    .getByRole("button", { name: "Make default for Copy for Paris" })
    .click();
  await expect
    .poll(
      async () => (await list()).find((one) => one.id === copyId)?.is_default,
    )
    .toBe(true);

  const rendered = await waitForRender(page.request, copyId, 1);
  await page.getByRole("link", { name: "Copy for Paris" }).click();
  const [download] = await Promise.all([
    page.waitForEvent("download"),
    page.getByRole("link", { name: "Download PDF" }).click(),
  ]);
  expect(download.suggestedFilename()).toMatch(/\.pdf$/);
  const pdf = await pdfOf(page.request, copyId, rendered.latest?.number ?? 0);
  expect(pdf.pages).toBe(1);
  expect(pdf.text).toContain(person.name);
  keep(EVIDENCE, "copy.pdf", pdf.bytes);
});

test("after a reload the list is the same and the copy is still the default", async () => {
  const before = (await list()).map((one) => [one.id, one.is_default]);
  await page.goto("/profile/cv");
  const items = page
    .getByRole("list", { name: "Your CVs" })
    .getByRole("listitem");
  await expect(items).toHaveCount(before.length);
  await expect(
    items.filter({ has: page.getByText("Default", { exact: true }) }),
  ).toHaveCount(1);
  await expect(
    items.filter({ has: page.getByText("Default", { exact: true }) }),
  ).toContainText("Copy for Paris");
  await page.reload();
  await expect(items).toHaveCount(before.length);
  expect((await list()).map((one) => [one.id, one.is_default])).toEqual(before);
  expect(errors).toEqual([]);
});
