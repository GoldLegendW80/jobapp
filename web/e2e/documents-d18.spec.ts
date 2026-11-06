import { execFileSync } from "node:child_process";

import { expect, test, type BrowserContext, type Page } from "@playwright/test";

import {
  addPosting,
  caddyUrl,
  invented,
  keep,
  readDocument,
  saveDraft,
  seedDocuments,
  signUp,
  waitForRender,
  type Person,
} from "./documents-helpers";
import { clearAttempts, newContext, root, watchErrors } from "./stack";

/**
 * Documents D18 against the real stack through Caddy, driven through the page: the gallery with
 * its "two columns" filter and its grid of two on a phone; a posting's CV switched to `ledger` from
 * the Template view, a new page drawn, its base CV unchanged; `photo-columns` set on the base CV from
 * the gallery, then a posting added afterwards whose CV takes `photo-columns`. No model runs: the base
 * CV and the documents are made in the api container (`seedDocuments`, and `postingCv` below) and
 * the CV draft is written by hand, so the real renderer draws every page.
 *
 * Locally, as documents-d6.spec.ts, then:
 *   E2E_CADDY_URL=http://127.0.0.1:8099 E2E_COMPOSE_PROJECT=jobapp-d18 \
 *     pnpm --dir web exec playwright test -c e2e/documents-all.config.ts documents-d18.spec.ts
 */
test.describe.configure({ mode: "serial" });
test.skip(!caddyUrl, "set E2E_CADDY_URL with the Compose stack running");

const EVIDENCE = "documents-d18";
const PHONE = { width: 390, height: 844 };

const POSTING_CV = `
import json, sys, uuid
from app.config import get_settings
from app.db import build_engine, build_sessionmaker
from app.documents import store
from app.documents.models import Kind
from app.identity import service as identity
email, posting = sys.argv[1], uuid.UUID(sys.argv[2])
if not (email.startswith("e2e-") and email.endswith("@example.org")):
    raise SystemExit("refusing a non e2e account")
engine = build_engine(get_settings())
try:
    with build_sessionmaker(engine)() as db, db.begin():
        user = identity.get_user_by_email(db, email)
        cv = store.get_or_create(db, user.id, posting, Kind.cv, at=identity.utcnow())
        out = {"cv": str(cv.id)}
finally:
    engine.dispose()
print(json.dumps(out))
`;

/** The CV of a posting, made as the writers make it when they start: it takes its base CV's template. */
function postingCv(person: Person, postingId: string): string {
  if (!/^e2e-[a-z0-9.-]+@example\.org$/.test(person.email))
    throw new Error(`refusing to seed documents for ${person.email}`);
  if (!/^[0-9a-f-]{36}$/.test(postingId)) throw new Error("not a posting id");
  const out = execFileSync(
    "docker",
    [
      "compose",
      "-p",
      process.env.E2E_COMPOSE_PROJECT ?? "jobapp",
      "-f",
      "compose.yml",
      "exec",
      "-T",
      "api",
      "python",
      "-c",
      POSTING_CV,
      person.email,
      postingId,
    ],
    { cwd: root, encoding: "utf8" },
  );
  return (JSON.parse(out.trim().split("\n").pop() ?? "") as { cv: string }).cv;
}

let person: Person;
let context: BrowserContext;
let page: Page;
let errors: string[];
let posting: string;
let base: string;
let cv: string;

test.beforeAll(async ({ browser }) => {
  person = invented("d18");
  context = await newContext(browser);
  page = await context.newPage();
  errors = watchErrors(page);
});

test.afterAll(async () => {
  await context.close();
  clearAttempts([person.email]);
});

test("an invented person with a base CV and a posting's CV drawn with band", async () => {
  await signUp(page, person);
  posting = await addPosting(page.request, person);
  ({ base, cv } = seedDocuments(person, posting));
  await saveDraft(page.request, cv, person.cvDraft);
  const drawn = await waitForRender(page.request, cv, 1);
  expect(drawn.latest?.template_slug).toBe("band");
});

test("the gallery lists the templates and filters those with two columns", async () => {
  await page.goto("/documents/templates");
  await expect(
    page.getByRole("heading", { level: 1, name: "Templates" }),
  ).toBeVisible();
  const all = page.getByRole("region", { name: "All templates" });
  await expect(all.getByRole("heading", { name: "Band" })).toBeVisible();
  const served = await all.getByRole("heading", { level: 3 }).count();
  expect(served).toBeGreaterThanOrEqual(3);

  await page.getByLabel("Columns").selectOption({ label: "Two columns" });
  await expect(page.getByText(/ of \d+ templates match\./)).toBeVisible();
  const cards = all.locator("li[data-testid^=template-]");
  const shown = await cards.count();
  expect(shown).toBeGreaterThan(0);
  expect(shown).toBeLessThan(served);
  for (let index = 0; index < shown; index += 1)
    await expect(cards.nth(index)).toContainText("two columns");
  await expect(all.getByRole("heading", { name: "Band" })).toHaveCount(0);

  const columns = page.getByTestId("template-photo-columns");
  await columns.getByRole("button", { name: "See the letter" }).click();
  await expect(
    columns.getByRole("img", { name: /, the letter$/ }),
  ).toBeVisible();
  keep(
    EVIDENCE,
    "gallery-two-columns.png",
    await page.screenshot({ fullPage: true }),
  );
});

test("on a phone the gallery is a grid of two", async () => {
  await page.setViewportSize(PHONE);
  await page.goto("/documents/templates");
  const cards = page
    .getByRole("region", { name: "All templates" })
    .locator("li[data-testid^=template-]");
  await expect(cards.nth(2)).toBeVisible();
  const [first, second, third] = await Promise.all(
    [0, 1, 2].map(async (index) => cards.nth(index).boundingBox()),
  );
  expect(first?.y).toBe(second?.y);
  expect(third?.y ?? 0).toBeGreaterThan(first?.y ?? 0);
  keep(
    EVIDENCE,
    "gallery-phone.png",
    await page.screenshot({ fullPage: true }),
  );
  await page.setViewportSize({ width: 1280, height: 900 });
});

test("a posting's CV switches to ledger in the Template view, and its base CV is unchanged", async () => {
  await page.goto(`/jobs/${posting}/documents/cv`);
  await page.getByRole("tab", { name: "Template" }).click();
  await expect(
    page.getByText(
      "Only this CV changes. The base CV it was written from keeps its template.",
    ),
  ).toBeVisible();
  const strip = page.getByRole("list", { name: "Templates" });
  await expect(strip.getByRole("button", { name: /^Band/ })).toHaveAttribute(
    "aria-pressed",
    "true",
  );
  await strip.getByRole("button", { name: /^Ledger/ }).click();
  await expect(strip.getByRole("button", { name: /^Ledger/ })).toHaveAttribute(
    "aria-pressed",
    "true",
  );

  const drawn = await waitForRender(page.request, cv, 2);
  expect(drawn.latest).toEqual(
    expect.objectContaining({
      number: 2,
      origin: "template",
      template_slug: "ledger",
      has_pdf: true,
    }),
  );
  await page.getByRole("tab", { name: "See" }).click();
  await expect(
    page.getByRole("img", { name: "Page 1 of version 2" }),
  ).toBeVisible({ timeout: 30_000 });
  keep(EVIDENCE, "cv-ledger.png", await page.screenshot({ fullPage: true }));
  expect((await readDocument(page.request, base)).template_slug).toBe("band");
});

test("photo-columns set on the base CV from the gallery is taken by a posting's CV made afterwards", async () => {
  await page.goto(`/documents/templates?document=${base}`);
  await expect(
    page.getByText(/^Choose the look of the base CV /),
  ).toBeVisible();
  await page
    .getByTestId("template-photo-columns")
    .getByRole("button", { name: "Use for this document" })
    .click();
  await expect(
    page.getByText(/^Photo and columns is now used for this document\./),
  ).toBeVisible();
  expect((await readDocument(page.request, base)).template_slug).toBe(
    "photo-columns",
  );
  // The CV written before keeps its own.
  expect((await readDocument(page.request, cv)).template_slug).toBe("ledger");

  const later = await addPosting(page.request, {
    ...person,
    posting: { ...person.posting, title: "Robotics platform engineer" },
  });
  const laterCv = postingCv(person, later);
  expect((await readDocument(page.request, laterCv)).template_slug).toBe(
    "photo-columns",
  );
  expect(errors).toEqual([]);
});
