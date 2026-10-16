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
 * Documents D13 against the real stack through Caddy, driven through the page: an invented
 * account with a made up model credential, the consent and a base CV (the writers on the fake
 * backend), a posting with fit 80 and one with fit 65. One pass of D10's tick
 * (`python -m app.documents.tick <email>` in the api container) starts the CV of the first, so
 * the Search tab's card says one posting was written today against the safety ceiling. Open
 * Advanced, see the count at 70, move the threshold to 60 and see what it adds before saving,
 * save; a ceiling of 501 is refused with its sentence; reload and see 60. The writing rules are
 * saved at once and read back after a reload. Screenshots at every viewport check that nothing
 * overflows on a phone.
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

/** One pass of the automatic generation for this account only. */
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

const card = () => page.getByTestId("documents-generation");
const advanced = () =>
  page.getByRole("button", { name: "Advanced", exact: true });
const slider = () => page.getByRole("slider", { name: "Fit score threshold" });
const figures = () => page.getByTestId("documents-threshold");

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

test("an invented person has a base CV, and the tick writes the posting at fit 80", async () => {
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
  const middle = await addPosting(page.request, {
    ...person,
    posting: { ...person.posting, title: "Support engineer" },
  });
  await setFit(middle, 65);

  expect(tick(person.email)).toContain("started 1");
});

test("the card shows today's count, and what a threshold of 60 adds before it is saved", async () => {
  await page.goto("/profile?tab=search");
  await expect(
    card().getByRole("switch", { name: "Write documents automatically" }),
  ).toBeChecked();
  await expect(card()).toContainText("For postings with a fit of 70 or more.");
  await expect(page.getByTestId("documents-today")).toContainText(
    "Today: 1 posting written, the safety ceiling is 100.",
  );
  await expect(page.getByTestId("documents-spend")).toBeVisible();

  await advanced().click();
  await expect(figures()).toContainText(
    "1 posting of the last 30 days has a fit of 70 or more.",
  );
  await slider().fill("60");
  await expect(figures()).toContainText(
    "2 postings of the last 30 days have a fit of 60 or more.",
  );
  await expect(page.getByTestId("documents-newly")).toContainText(
    "Lowering it to 60 adds 1 posting to write, about",
  );
  await shots(page, "threshold-60");

  const ceiling = page.getByLabel("Safety ceiling per day");
  await ceiling.fill("501");
  await page.getByRole("button", { name: "Save search" }).click();
  await expect(
    page.getByText(
      "The safety ceiling is a whole number of postings a day, from 1 to 500.",
    ),
  ).toBeVisible();
  await ceiling.fill("100");
  await page.getByRole("button", { name: "Save search" }).click();
  await expect(page.getByText("Search saved.")).toBeVisible();
});

test("after a reload the threshold is 60", async () => {
  await page.reload();
  await expect(card()).toContainText("For postings with a fit of 60 or more.");
  await advanced().click();
  await expect(slider()).toHaveValue("60");
  await expect(figures()).toContainText(
    "2 postings of the last 30 days have a fit of 60 or more.",
  );
  await expect(page.getByTestId("documents-newly")).toHaveCount(0);
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
  });
  expect(errors).toEqual([]);
});
