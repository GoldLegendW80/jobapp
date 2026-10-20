import fs from "node:fs";
import path from "node:path";

import {
  expect,
  test,
  type BrowserContext,
  type Locator,
  type Page,
} from "@playwright/test";

import {
  CSRF,
  caddyUrl,
  invented,
  signUp,
  type Person,
} from "./documents-helpers";
import { makePdf } from "./ingestion-pdf";
import {
  clearAttempts,
  makeShots,
  newContext,
  root,
  signIn,
  signOut,
  watchErrors,
} from "./stack";

/**
 * Opportunities O5 against the real stack through Caddy: the first login wizard. An invented
 * admin (the owner's kind of account) with a filled profile signs up: never sent to the wizard,
 * no "Finish setting up". The admin invites two invented members, N and B. N accepts at phone
 * width (375 px) and lands on /welcome; by keyboard only (focus, typing, Space on the choices,
 * Enter on the buttons) N uploads an invented CV (a PDF written by ./ingestion-pdf.ts from
 * api/tests/documents/fixtures/upload_morgan.txt) through the CV upload panel, agrees to what is
 * sent, sees it converted on the fake model backend, then fills the roles, the places and the
 * contracts; Back shows every value typed; a reload and a fresh visit of /welcome open the step
 * left; the pay is "Rather not say", Full auto shows as "Available soon" and cannot be chosen,
 * and "Start" on the summary ends on Discover. The profile holds the answers and apply's mode is
 * still Review first. Signing in again lands on the home page with no card. Person B, beside N,
 * still gets the wizard, puts it off with "Skip for now", and after signing in again is not
 * sent to it but sees the card. No screen shows a dash.
 *
 * Locally, as opportunities-all.config.ts says (AGENTS_FAKE_BACKEND=1 on the worker), then:
 *   E2E_CADDY_URL=... E2E_ATS_FORMS_URL=... E2E_COMPOSE_PROJECT=... \
 *     pnpm --dir web exec playwright test -c e2e/opportunities-all.config.ts opportunities-o5.spec.ts
 */
test.describe.configure({ mode: "serial" });
test.skip(!caddyUrl, "set E2E_CADDY_URL with the Compose stack running");

const DASH = /[‒–—―]| - /;
const KEY = `sk-ant-api03-e2egood${"Dn9x".repeat(20)}Rn42`;
const PASSWORD = "Wizard-journey-2026!";
const FILE = "morgan-exampleton-cv.pdf";
const LINES = fs
  .readFileSync(
    path.join(root, "api/tests/documents/fixtures/upload_morgan.txt"),
    "utf8",
  )
  .trimEnd()
  .split("\n");
const shotsAt = makeShots("opportunities-o5", "o5");
/** The screenshots (both widths, both schemes, no sideways scroll), then back to the phone. */
async function shots(page: Page, name: string) {
  await shotsAt(page, name);
  await page.setViewportSize({ width: 375, height: 740 });
}

type State = {
  open_wizard: boolean;
  show_card: boolean;
  completed_at: string | null;
};

let admin: Person;
let adminContext: BrowserContext;
let adminPage: Page;
const run = `${Date.now().toString(36)}${Math.random().toString(36).slice(2, 6)}`;
const N = { email: `e2e-o5-new-${run}@example.org`, name: "Robin Sampleton" };
const B = {
  email: `e2e-o5-other-${run}@example.org`,
  name: "Alex Placeholder",
};
let contextN: BrowserContext;
let pageN: Page;
let contextB: BrowserContext;
let pageB: Page;
let errors: string[];

async function noDash(page: Page) {
  const text = await page.evaluate(() => document.body.innerText);
  expect(text).not.toMatch(DASH);
}

async function stateOf(page: Page): Promise<State> {
  const read = await page.request.get("/api/opportunities/onboarding");
  expect(read.status()).toBe(200);
  return (await read.json()) as State;
}

async function invite(email: string): Promise<string> {
  const made = await adminPage.request.post("/api/admin/invites", {
    headers: CSRF,
    data: { email },
  });
  expect(made.status()).toBe(201);
  return ((await made.json()) as { path: string }).path;
}

/** Accept the invite on `page` and wait for the app's first login decision. */
async function accept(page: Page, link: string, name: string) {
  await page.goto(link);
  await page.getByLabel("Name").fill(name);
  await page.getByLabel(/^(New password|Password)$/).fill(PASSWORD);
  await page.getByLabel("Confirm password").fill(PASSWORD);
  await page.getByLabel("Confirm password").press("Enter");
  await expect(page.locator("html[data-onboarding]")).toHaveCount(1);
}

async function press(target: Locator, key: "Enter" | "Space") {
  await target.focus();
  await target.press(key);
}

const step = (page: Page, number: number) =>
  expect(
    page.getByRole("heading", {
      level: 2,
      name: new RegExp(`^Step ${number} of 7`),
    }),
  ).toBeVisible();
const next = (page: Page) =>
  press(page.getByRole("button", { name: /^Next/ }), "Enter");
const back = (page: Page) =>
  press(page.getByRole("button", { name: /Back/ }), "Enter");

test.beforeAll(async ({ browser }) => {
  admin = invented("o5");
  adminContext = await newContext(browser);
  adminPage = await adminContext.newPage();
  contextN = await newContext(browser);
  pageN = await contextN.newPage();
  await pageN.setViewportSize({ width: 375, height: 740 });
  errors = watchErrors(pageN);
  contextB = await newContext(browser);
  pageB = await contextB.newPage();
});

test.afterAll(async () => {
  await Promise.all([adminContext.close(), contextN.close(), contextB.close()]);
  clearAttempts([admin.email, N.email, B.email]);
});

test("an admin with a filled profile is never sent to the wizard", async () => {
  await signUp(adminPage, admin);
  await expect(adminPage.locator("html[data-onboarding]")).toHaveAttribute(
    "data-onboarding",
    "stay",
  );
  await adminPage.reload();
  await expect(
    adminPage.getByRole("heading", { level: 1, name: "Jobs" }),
  ).toBeVisible();
  await expect(adminPage).toHaveURL(/\/jobs$/);
  expect(await stateOf(adminPage)).toMatchObject({
    open_wizard: false,
    show_card: false,
  });
  await expect(
    adminPage.getByRole("region", { name: "Finish setting up" }),
  ).toHaveCount(0);
});

test("a new member lands on the wizard and imports an invented CV in step 1", async () => {
  await accept(pageN, await invite(N.email), N.name);
  await expect(pageN).toHaveURL(/\/welcome$/);
  await step(pageN, 1);
  const saved = await pageN.request.put("/api/me/model-credential", {
    headers: CSRF,
    data: { kind: "anthropic_api_key", secret: KEY },
  });
  expect(saved.status()).toBe(200);
  await noDash(pageN);
  await shots(pageN, "step-1");

  await pageN.getByTestId("upload-cv-file").setInputFiles({
    name: FILE,
    mimeType: "application/pdf",
    buffer: makePdf([{ lines: LINES }]),
  });
  const consent = pageN.getByRole("dialog", {
    name: "What is sent to build your CV",
  });
  await expect(consent).toBeVisible({ timeout: 60_000 });
  await press(consent.getByRole("checkbox"), "Space");
  await press(
    consent.getByRole("button", { name: "Agree and build my CV" }),
    "Enter",
  );
  await expect(pageN.getByTestId("upload-result")).toContainText(
    "is ready to edit",
    {
      timeout: 120_000,
    },
  );
  await next(pageN);
});

test("steps 2 to 4 by keyboard, and Back keeps every value", async () => {
  await step(pageN, 2);
  await expect(
    pageN.getByRole("button", { name: "Skip this step" }),
  ).toHaveCount(0);
  await pageN.getByLabel("Roles").focus();
  await pageN.keyboard.type("Data engineer, Analytics engineer");
  await press(pageN.getByRole("checkbox", { name: "Senior" }), "Space");
  await noDash(pageN);
  await next(pageN);

  await step(pageN, 3);
  await pageN.getByLabel("Places").focus();
  await pageN.keyboard.type("Lyon");
  await press(pageN.getByRole("radio", { name: "Hybrid" }), "Space");
  await noDash(pageN);
  await next(pageN);

  await step(pageN, 4);
  await back(pageN);
  await step(pageN, 3);
  await expect(pageN.getByLabel("Places")).toHaveValue("Lyon");
  await expect(pageN.getByRole("radio", { name: "Hybrid" })).toBeChecked();
  await back(pageN);
  await step(pageN, 2);
  await expect(pageN.getByLabel("Roles")).toHaveValue(
    "Data engineer, Analytics engineer",
  );
  await expect(pageN.getByRole("checkbox", { name: "Senior" })).toBeChecked();
  await next(pageN);
  await step(pageN, 3);
  await next(pageN);

  await step(pageN, 4);
  await press(
    pageN.getByRole("checkbox", { name: "Freelance mission" }),
    "Space",
  );
  await press(pageN.getByRole("checkbox", { name: "Permanent" }), "Space");
  await noDash(pageN);
  await shots(pageN, "step-4");
  await next(pageN);
  await step(pageN, 5);
});

test("a reload, then a fresh visit of /welcome, open the step left", async () => {
  await pageN.reload();
  await step(pageN, 5);
  await pageN.goto("/welcome");
  await step(pageN, 5);
});

test("pay, how the app applies, the summary, and Start opens Discover", async () => {
  await press(pageN.getByRole("checkbox", { name: "Rather not say" }), "Space");
  await noDash(pageN);
  await next(pageN);

  await step(pageN, 6);
  const full = pageN.getByRole("radio", { name: /Full auto/ });
  await expect(full).toBeDisabled();
  await expect(full).not.toBeChecked();
  await expect(pageN.getByText("Available soon")).toBeVisible();
  await expect(
    pageN.getByRole("radio", { name: /Review first/ }),
  ).toBeChecked();
  await press(pageN.getByRole("radio", { name: "Warm" }), "Space");
  await noDash(pageN);
  await shots(pageN, "step-6");
  await next(pageN);

  await step(pageN, 7);
  const summary = pageN.locator("dl");
  await expect(summary).toContainText("Data engineer, Analytics engineer");
  await expect(summary).toContainText("Lyon; Hybrid");
  await expect(summary).toContainText("Freelance mission, Permanent");
  await expect(summary).toContainText("Rather not say");
  await noDash(pageN);
  await shots(pageN, "summary");
  await press(pageN.getByRole("button", { name: "Start" }), "Enter");
  await expect(pageN).toHaveURL(/\/discover$/);
  await expect(
    pageN.getByRole("heading", { level: 1, name: "Discover" }),
  ).toBeVisible();

  const profile = (await (await pageN.request.get("/api/profile")).json()) as {
    sections: {
      search: Record<string, unknown>;
      preferences: { documents_tone: string };
    };
  };
  expect(profile.sections.search).toMatchObject({
    title_include: { terms: ["Data engineer", "Analytics engineer"] },
    seniority: ["senior"],
    location: { terms: ["Lyon"] },
    remote_stance: "hybrid",
    contract_types: ["Freelance", "CDI"],
    compensation: { salary: null, daily_rate: null },
  });
  expect(profile.sections.preferences.documents_tone).toBe("warm");
  const settings = (await (
    await pageN.request.get("/api/apply/settings")
  ).json()) as {
    mode_after_like: string;
  };
  expect(settings.mode_after_like).toBe("review");
  expect((await stateOf(pageN)).completed_at).not.toBeNull();
});

test("signing in again lands on the home page, with no card", async () => {
  await signOut(pageN, N.name);
  await signIn(pageN, N.email, PASSWORD);
  await expect(pageN).toHaveURL(/\/jobs$/);
  await expect(pageN.locator("html[data-onboarding]")).toHaveAttribute(
    "data-onboarding",
    "stay",
  );
  await expect(
    pageN.getByRole("region", { name: "Finish setting up" }),
  ).toHaveCount(0);
  expect(errors).toEqual([]);
});

test("person B still gets the wizard, puts it off, and then sees only the card", async () => {
  await accept(pageB, await invite(B.email), B.name);
  await expect(pageB).toHaveURL(/\/welcome$/);
  expect(await stateOf(pageB)).toMatchObject({
    open_wizard: true,
    completed_at: null,
  });
  await press(pageB.getByRole("button", { name: "Skip for now" }), "Enter");
  await expect(pageB).toHaveURL(/\/jobs$/);
  await signOut(pageB, B.name);
  await signIn(pageB, B.email, PASSWORD);
  await expect(pageB).toHaveURL(/\/jobs$/);
  await expect(pageB.locator("html[data-onboarding]")).toHaveAttribute(
    "data-onboarding",
    "stay",
  );
  const card = pageB.getByRole("region", { name: "Finish setting up" });
  await expect(card).toBeVisible();
  await noDash(pageB);
  await shots(pageB, "card");
  await card.getByRole("link", { name: "Continue setup" }).click();
  await expect(pageB).toHaveURL(/\/welcome$/);
  await step(pageB, 1);
});
