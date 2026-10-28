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
 * contracts; Back shows every value typed. N's account is then given skills. A reload, a fresh visit of /welcome and a
 * second device (a new browser context signed in as N) open the step left, since the steps
 * passed are kept in the profile. The pay is "Rather not say", Full auto shows as "Available
 * soon" and cannot be chosen, and "Start" on the summary opens Discover with the sentence that
 * asks to tick job websites in Find jobs, and starts no sweep: every search runs the ticked
 * websites alone (owner, 2026-10-28), and N has no website. The profile holds the
 * answers and apply's mode is still Review first. Signing in again lands on the home page with
 * no card. Person B, beside N, still gets the wizard, puts it off with "Skip for now", and after
 * signing in again is not sent to it but sees the card; B has no website either, so B's
 * Start ends the wizard the same way. No screen shows a dash.
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
  passed: string[];
};

/** The skills N is given ("Python, agents, LLM evaluation"). */
const SKILLS = {
  years_experience: null,
  have: [
    { name: "python", terms: ["python"] },
    { name: "agents", terms: ["agent", "agents"] },
    { name: "llm", terms: ["llm", "evaluation"] },
  ],
  adjacent: [],
  none: [],
  gates: [],
};
// Every search runs the ticked job websites alone (owner, 2026-10-28): with none ticked, Start
// ends the wizard without a sweep and says why.
const NO_WEBSITES =
  "Tick the job websites to search in Find jobs, then find new postings.";

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

/** Replace one section of the signed in person's profile, from its current revision. */
async function putSection(
  page: Page,
  section: string,
  change: (stored: Record<string, unknown>) => Record<string, unknown>,
) {
  const read = await page.request.get("/api/profile");
  const profile = (await read.json()) as {
    revision: number;
    sections: Record<string, Record<string, unknown> | null>;
  };
  const saved = await page.request.put(`/api/profile/${section}`, {
    headers: CSRF,
    data: {
      base_revision: profile.revision,
      data: change(profile.sections[section] ?? {}),
    },
  });
  expect(saved.status(), await saved.text()).toBe(200);
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

test("wizard route variants stay put and wizard return targets fall back home", async ({
  browser,
}) => {
  for (const [index, pathname] of ["/Welcome", "/%57elcome"].entries()) {
    const context = await newContext(browser);
    const email = `e2e-o5-path-${index}-${run}@example.org`;
    try {
      const page = await context.newPage();
      const pathErrors = watchErrors(page);
      await accept(page, await invite(email), "Example Member");
      await page.evaluate(() => {
        window.sessionStorage.clear();
      });
      const path = `${pathname}?${new URLSearchParams({ next: pathname, step: "profile" })}#upload`;
      await page.goto(path);
      await expect(
        page.getByRole("heading", { level: 1, name: "Welcome" }),
      ).toBeVisible();
      expect(
        new URL(page.url()).pathname +
          new URL(page.url()).search +
          new URL(page.url()).hash,
      ).toBe(path);
      await expect(page.locator("html")).toHaveAttribute(
        "data-onboarding",
        "stay",
      );
      await shots(page, `wizard-path-${index}`);
      await press(page.getByRole("button", { name: "Skip for now" }), "Enter");
      await expect(page).toHaveURL(/\/jobs$/);
      expect(pathErrors).toEqual([]);
    } finally {
      clearAttempts([email]);
      await context.close();
    }
  }
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

  // A LinkedIn export or a pasted text: stored and read from the step itself (#484), up to the
  // estimate; the run is not started here (it would ask the model).
  await pageN.getByRole("button", { name: "Advanced" }).click();
  await pageN
    .getByLabel("Or paste a text")
    .fill("Robin Sampleton, data engineer at Example Robotics since 2023.");
  await pageN.getByRole("button", { name: "Add this text" }).click();
  const read = pageN.getByRole("region", {
    name: "Read them into your profile",
  });
  await press(read.getByRole("button", { name: "Select all" }), "Enter");
  await press(read.getByRole("button", { name: "See the estimate" }), "Enter");
  const estimate = pageN.getByRole("dialog", { name: "Before the run starts" });
  await expect(estimate.getByTestId("estimate")).toBeVisible({
    timeout: 60_000,
  });
  await noDash(pageN);
  await pageN.keyboard.press("Escape");
  await expect(estimate).toBeHidden();
  await next(pageN);
});

test("steps 2 to 4 by keyboard, and Back keeps every value", async () => {
  await step(pageN, 2);
  await expect(
    pageN.getByRole("button", { name: "Skip this step" }),
  ).toHaveCount(0);
  await pageN.getByLabel("Roles").focus();
  await pageN.keyboard.type("AI engineer, Applied AI engineer");
  await press(pageN.getByRole("button", { name: "Advanced" }), "Enter");
  await press(pageN.getByRole("checkbox", { name: "Senior" }), "Space");
  await noDash(pageN);
  await next(pageN);

  await step(pageN, 3);
  await pageN.getByLabel("Places").focus();
  await pageN.keyboard.type("Paris");
  await press(pageN.getByRole("radio", { name: "Hybrid" }), "Space");
  await noDash(pageN);
  await next(pageN);

  await step(pageN, 4);
  await back(pageN);
  await step(pageN, 3);
  await expect(pageN.getByLabel("Places")).toHaveValue("Paris");
  await expect(pageN.getByRole("radio", { name: "Hybrid" })).toBeChecked();
  await back(pageN);
  await step(pageN, 2);
  await expect(pageN.getByLabel("Roles")).toHaveValue(
    "AI engineer, Applied AI engineer",
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

test("a reload, a fresh visit of /welcome and another device open the step left", async ({
  browser,
}) => {
  // The account's skills.
  await putSection(pageN, "skills", () => SKILLS);
  await pageN.reload();
  await step(pageN, 5);
  await pageN.goto("/welcome");
  await step(pageN, 5);
  expect((await stateOf(pageN)).passed).toEqual([
    "profile",
    "looking",
    "where",
    "work",
  ]);
  // Another device: the steps passed are in the profile, not in a browser.
  const other = await newContext(browser);
  try {
    const page = await other.newPage();
    await signIn(page, N.email, PASSWORD);
    await expect(page).toHaveURL(/\/jobs$/);
    await page.goto("/welcome");
    await step(page, 5);
  } finally {
    await other.close();
  }
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
  await expect(summary).toContainText("AI engineer, Applied AI engineer");
  await expect(summary).toContainText("Paris; Hybrid");
  await expect(summary).toContainText("Freelance mission, Permanent");
  await expect(summary).toContainText("Rather not say");
  // Every search runs the job websites ticked in Find jobs alone (owner, 2026-10-28): N ticked
  // none, so the first search covers no website.
  await expect(
    pageN.getByText(/covers 0 job websites ticked in Find jobs/),
  ).toBeVisible();
  await noDash(pageN);
  await shots(pageN, "summary");
  await press(pageN.getByRole("button", { name: "Start" }), "Enter");
  await expect(pageN.getByText(NO_WEBSITES)).toBeVisible();
  await expect(pageN).toHaveURL(/\/discover$/);
  await expect(
    pageN.getByRole("heading", { level: 1, name: "Discover" }),
  ).toBeVisible();
  const sweeps = await pageN.request.get("/api/jobs/sweeps?limit=1");
  expect(((await sweeps.json()) as { items: unknown[] }).items).toEqual([]);

  const profile = (await (await pageN.request.get("/api/profile")).json()) as {
    sections: {
      search: Record<string, unknown>;
      preferences: { documents_tone: string };
    };
  };
  expect(profile.sections.search).toMatchObject({
    title_include: { terms: ["AI engineer", "Applied AI engineer"] },
    seniority: ["senior"],
    location: { terms: ["Paris"] },
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
  // The summary typed in the address opens the first step left while no role is saved.
  await pageB.goto("/welcome?step=summary");
  await step(pageB, 1);
  await expect(pageB.getByRole("button", { name: "Start" })).toHaveCount(0);
});

test("B, with roles and no website, ends the wizard without a sweep", async () => {
  await putSection(pageB, "search", (stored) => ({
    ...stored,
    title_include: { terms: ["Analytics engineer"], advanced_regex: false },
  }));
  await pageB.goto("/welcome?step=summary");
  await step(pageB, 7);
  await expect(
    pageB.getByText(/covers 0 job websites ticked in Find jobs/),
  ).toBeVisible();
  await press(pageB.getByRole("button", { name: "Start" }), "Enter");
  await expect(pageB.getByText(NO_WEBSITES)).toBeVisible();
  await expect(pageB).toHaveURL(/\/discover$/);
  expect((await stateOf(pageB)).completed_at).not.toBeNull();
  const sweeps = await pageB.request.get("/api/jobs/sweeps?limit=1");
  expect(((await sweeps.json()) as { items: unknown[] }).items).toEqual([]);
});
