import { execFileSync } from "node:child_process";

import { expect, test, type BrowserContext, type Page } from "@playwright/test";

import {
  CSRF,
  addPosting,
  caddyUrl,
  invented,
  signUp,
  waitForRender,
  type Person,
} from "./documents-helpers";
import {
  clearAttempts,
  makeShots,
  newContext,
  root,
  watchErrors,
} from "./stack";

/**
 * Documents D29 (in D13's journey) against the real stack through Caddy, driven through the page:
 * an invented account with a made up model credential, the consent and a base CV (the writers on
 * the fake backend), a posting with fit 80. One pass of the documents tick
 * (`python -m app.documents.tick <email>` in the api container) writes nothing: no document is
 * written without the person's click. The Search tab has no documents card any more and keeps
 * the writing rules, saved at once and read back after a reload. Screenshots at every viewport
 * check that nothing overflows on a phone.
 *
 * Locally, as documents-d6.spec.ts (AGENTS_FAKE_BACKEND=1 on the worker), then:
 *   E2E_CADDY_URL=http://127.0.0.1:8099 E2E_COMPOSE_PROJECT=jobapp-d13 \
 *     pnpm --dir web exec playwright test -c e2e/documents-all.config.ts documents-d13.spec.ts
 */
test.describe.configure({ mode: "serial" });
test.skip(!caddyUrl, "set E2E_CADDY_URL with the Compose stack running");

const EVIDENCE = "documents-d13";
const KEY = `sk-ant-api03-e2egood${"Dn9x".repeat(20)}Rn42`;
const shots = makeShots(EVIDENCE, "d13");

let person: Person;
let context: BrowserContext;
let page: Page;
let errors: string[];

/** One pass of the documents tick for this account only. */
function tick(email: string): string {
  return execFileSync(
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
      "-m",
      "app.documents.tick",
      email,
    ],
    { cwd: root, encoding: "utf8" },
  );
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
  person = invented("d13");
  context = await newContext(browser);
  page = await context.newPage();
  errors = watchErrors(page);
});

test.afterAll(async () => {
  await context.close();
  clearAttempts([person.email]);
});

test("an invented person has a base CV, and the tick writes nothing for a posting at fit 80", async () => {
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

  const high = await addPosting(page.request, person);
  await setFit(high, 80);

  expect(tick(person.email)).toMatch(/^started 0,/);
  const read = await page.request.get(`/api/jobs/${high}/documents`);
  const { documents } = (await read.json()) as { documents: unknown[] };
  expect(documents).toEqual([]);
  await page.goto(`/jobs/${high}`);
  await expect(page.getByTestId("posting-cv")).toContainText(
    "Not written yet. It is written when you click Write or Apply.",
  );
  await shots(page, "written-on-click");
});

test("the Search tab has no documents card", async () => {
  await page.goto("/settings/search");
  await expect(
    page.getByRole("heading", { name: "Writing rules" }),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "Documents for new postings" }),
  ).toHaveCount(0);
  await expect(
    page.getByRole("slider", { name: "Fit score threshold" }),
  ).toHaveCount(0);
});

test("the writing rules are saved at once and read back after a reload", async () => {
  const dashes = page.getByRole("switch", { name: "No dashes as separators" });
  await dashes.check();
  await expect(page.getByText("Writing rules saved.")).toBeVisible();
  const phrase = page.getByRole("textbox", { name: "New banned phrase" });
  await phrase.fill("passionate about");
  await phrase.press("Enter");
  const list = page.getByRole("list", { name: "Banned phrases" });
  await expect(list).toContainText("passionate about");
  await shots(page, "writing-rules");

  await page.reload();
  await expect(
    page.getByRole("switch", { name: "No dashes as separators" }),
  ).toBeChecked();
  await expect(
    page.getByRole("list", { name: "Banned phrases" }),
  ).toContainText("passionate about");
  const read = await page.request.get("/api/documents/settings");
  expect(await read.json()).toEqual({
    no_dash_separators: true,
    banned_phrases: ["passionate about"],
    starter_phrases: false,
    starter_list: expect.any(Array),
  });
  expect(errors).toEqual([]);
});
