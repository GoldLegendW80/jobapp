import { readFileSync } from "node:fs";
import path from "node:path";

import { expect, test, type BrowserContext, type Page } from "@playwright/test";

import {
  caddyUrl,
  clearAttempts,
  cliLink,
  makeShots,
  newContext,
  root,
  setPassword,
  watchErrors,
} from "./stack";

/**
 * Task G13 of the ingestion module against the real stack through Caddy, no mocks, with the
 * fake backend (AGENTS_FAKE_BACKEND=true, never in production): the review of what a run
 * proposes. A new user adds the two documents of the G12 journey and one of this journey's own
 * (a headline and four answers, so a section holds what a bulk accept may take), a run reads
 * them to `done`, and the user decides in the page: accept one, edit and accept one (the
 * server's refusal shown at the field first), reject one, accept a whole section (exactly one
 * revision in the History drawer of the profile), accept a locked employer on its own, pick a
 * value in a conflict, reject the rest of a section in one click. The completeness bar rises
 * and every decision is still there after a reload.
 *
 * It sets up its own user, documents and run, and depends on no other journey.
 *
 * Locally, on a scratch Compose project (its own volumes and images, torn down afterwards):
 *   .github/scripts/ci_env.sh 8113 >.env && echo AGENTS_FAKE_BACKEND=true >>.env
 *   docker compose -p jobapp-g13 -f compose.yml -f compose.mac.yml up -d --build --wait
 *   docker compose -p jobapp-g13 -f compose.yml exec -T api alembic upgrade head
 *   E2E_CADDY_URL=http://127.0.0.1:8113 E2E_COMPOSE_PROJECT=jobapp-g13 \
 *     pnpm --dir web exec playwright test -c e2e/ingestion-all.config.ts ingestion-g13
 *   docker compose -p jobapp-g13 -f compose.yml down -v
 */
test.describe.configure({ mode: "serial" });
test.skip(!caddyUrl, "set E2E_CADDY_URL with the Compose stack running");

const stamp = Date.now().toString(36);
const EMAIL = `e2e-ingestion-g13-${stamp}@example.org`;
const NAME = "Sam Example";
const PASSWORD = `ingestion review ${stamp} passphrase`;
const tail = () => Math.random().toString(36).slice(2, 12).padEnd(10, "x");
const KEY = `sk-ant-api03-e2egood${tail()}${tail()}Rn42`;

const RUNS = "/api/ingestion/runs";
const CSRF = { "X-Requested-With": "jobapp" };
const FIXTURES = path.join(root, "api/tests/ingestion/fixtures");
const FILES = ["g12_first.txt", "g12_second.txt", "g13_notes.txt"];
const RAIL = "employers:example-rail-2019";
const POST = "employers:example-post-2016";
const YEARS = "years_experience";
const DASH = /[–—]| - /;

const shots = makeShots("ingestion-g13", "g13");

let context: BrowserContext;
let page: Page;
let errors: string[];
let before = 0;

test.beforeAll(async ({ browser }) => {
  context = await newContext(browser);
  page = await context.newPage();
  // The edit refused on purpose answers 422: the browser logs it, the journey expects it.
  errors = watchErrors(page, [[422, /\/api\/proposals\/[^/]+\/accept$/]]);
});

test.afterAll(async () => {
  await context.close();
  clearAttempts([EMAIL]);
});

const proposal = (key: string) =>
  page.locator(`[data-testid="proposal"][data-key="${key}"]`);
const section = (name: string) => page.getByRole("region", { name });
const bar = () =>
  page.getByRole("progressbar", { name: "Profile completeness" });
const completeness = async () =>
  Number(await bar().getAttribute("aria-valuenow"));

async function profile() {
  const response = await context.request.get("/api/profile");
  expect(response.status()).toBe(200);
  return (await response.json()) as {
    revision: number;
    sections: Record<string, Record<string, unknown>>;
  };
}

async function openReview() {
  await page.goto("/profile/fill?step=review");
  await expect(page.getByTestId("proposal").first()).toBeVisible();
}

test("a run on three documents proposes nine changes", async () => {
  await page.goto(cliLink(EMAIL));
  await setPassword(page, PASSWORD, NAME);
  const saved = await context.request.put("/api/me/model-credential", {
    headers: CSRF,
    data: { kind: "anthropic_api_key", secret: KEY },
  });
  expect(saved.status()).toBe(200);

  await page.goto("/profile/fill?step=documents");
  await page.getByTestId("document-files").setInputFiles(
    FILES.map((name) => ({
      name,
      mimeType: "text/plain",
      buffer: readFileSync(path.join(FIXTURES, name)),
    })),
  );
  const rows = page
    .getByRole("list", { name: "Your documents" })
    .getByRole("listitem");
  for (const name of FILES)
    await expect(rows.filter({ hasText: name })).toContainText("Ready");
  const listed = await context.request.get("/api/ingestion/documents");
  const documents = (
    (await listed.json()) as { items: { id: string }[] }
  ).items.map((one) => one.id);
  expect(documents).toHaveLength(FILES.length);

  const estimate = async () => {
    const response = await context.request.post(`${RUNS}/estimate`, {
      headers: CSRF,
      data: { document_ids: documents, reread_document_ids: [] },
    });
    expect(response.status()).toBe(200);
    return (await response.json()) as {
      consent: { version: number };
      quote_id: string;
    };
  };
  const given = await context.request.post("/api/ingestion/consent", {
    headers: CSRF,
    data: { version: (await estimate()).consent.version },
  });
  expect(given.status()).toBe(200);
  const started = await context.request.post(RUNS, {
    headers: CSRF,
    data: {
      document_ids: documents,
      reread_document_ids: [],
      quote_id: (await estimate()).quote_id,
    },
  });
  expect(started.status()).toBe(201);
  const runId = ((await started.json()) as { id: string }).id;
  interface Run {
    status: string;
    counts: { proposals: number };
  }
  let run: Run | undefined;
  await expect
    .poll(
      async () => {
        run = (await (
          await context.request.get(`${RUNS}/${runId}`)
        ).json()) as Run;
        return run.status;
      },
      {
        message: "the run ends",
        timeout: 150_000,
        intervals: [250, 500, 1_000],
      },
    )
    .toBe("done");
  // The name, the headline, two employers, the derived years and four answers.
  expect(run?.counts.proposals).toBe(9);
});

test("accept one proposal: the completeness bar rises", async () => {
  await openReview();
  await expect(page.getByTestId("proposal")).toHaveCount(9);
  await expect(bar()).toBeVisible();
  before = await completeness();
  await shots(page, "review");

  const name = proposal("full_name");
  await expect(name).toContainText("Accepted one by one");
  await name.getByRole("button", { name: "Accept: Full name" }).click();
  await expect(name).toHaveAttribute("data-status", "accepted");
  await expect(name.getByRole("button", { name: /Accept|Reject/ })).toHaveCount(
    0,
  );
  await expect.poll(completeness).toBeGreaterThan(before);
  expect((await profile()).sections.identity?.full_name).toBe("Sam Rivera");
});

test("edit a proposal: the server's refusal is shown at the field, then the edit is accepted", async () => {
  const headline = proposal("headline");
  await headline.getByRole("button", { name: "Edit: Headline" }).click();
  const form = headline.getByRole("form", { name: "Edit Headline" });
  const field = form.getByLabel("Headline");
  await expect(field).toBeFocused();
  await field.fill("x".repeat(201));
  await form.getByRole("button", { name: "Accept with my changes" }).click();
  await expect(form.getByRole("alert")).toBeVisible();
  await expect(field).toHaveAttribute("aria-invalid", "true");
  await expect(headline).toHaveAttribute("data-status", "pending");
  await shots(page, "edit-refused");

  await field.fill("Lead data engineer");
  await field.press("Enter");
  await expect(headline).toHaveAttribute("data-status", "accepted");
  await expect(headline).toContainText("Lead data engineer");
  expect((await profile()).sections.identity?.headline).toBe(
    "Lead data engineer",
  );
});

test("reject one proposal with the keyboard alone", async () => {
  const travel = proposal("travel");
  await travel.getByRole("button", { name: "Reject: Travel" }).focus();
  await page.keyboard.press("Enter");
  await expect(travel).toHaveAttribute("data-status", "rejected");
  // The focus stays on the proposal that was decided.
  await expect(travel.getByRole("heading", { name: "Travel" })).toBeFocused();
  expect((await profile()).sections.answers?.travel ?? null).toBeNull();
});

test("accept a whole section: one revision in the History drawer of the profile", async () => {
  const revision = (await profile()).revision;
  const answers = section("Answers");
  await answers
    .getByRole("button", { name: "Accept all in this section: Answers" })
    .click();
  await expect(answers.getByRole("status")).toHaveText("Accepted 3.");
  for (const key of ["notice_period", "earliest_start", "relocation"])
    await expect(proposal(key)).toHaveAttribute("data-status", "accepted");
  await expect(proposal("travel")).toHaveAttribute("data-status", "rejected");
  const held = await profile();
  expect(held.revision).toBe(revision + 1);
  expect(held.sections.answers).toMatchObject({
    notice_period: "Two months",
    earliest_start: "The first of March",
    relocation: "Open to moving within France",
  });

  await page.goto("/profile");
  await page.getByRole("tab", { name: /^Answers,/ }).click();
  await page.getByRole("button", { name: "History of answers" }).click();
  const versions = page
    .getByRole("dialog")
    .getByLabel("Versions of answers")
    .getByRole("listitem");
  await expect(versions).toHaveCount(1);
  await expect(versions).toContainText(`Revision ${String(revision + 1)}`);
  await shots(page, "history-one-revision");
});

test("accept a locked employer on its own, and pick a value in a conflict", async () => {
  await openReview();
  const experience = section("Experience");
  // Nothing here goes in bulk: both are locked facts, one of them a conflict.
  await expect(
    experience.getByRole("button", { name: /Accept all/ }),
  ).toHaveCount(0);
  const post = proposal(POST);
  await post
    .getByRole("button", { name: "Accept: Employers: Example Post" })
    .click();
  await expect(post).toHaveAttribute("data-status", "accepted");

  const rail = proposal(RAIL);
  await expect(rail).toContainText("Sources disagree");
  const accept = rail.getByRole("button", {
    name: "Accept: Employers: Example Rail",
  });
  await expect(accept).toBeDisabled();
  const values = rail.getByRole("radiogroup", {
    name: "Values for Employers: Example Rail",
  });
  await expect(values.getByRole("radio")).toHaveCount(2);
  await expect(values).toContainText("g12_first.txt");
  await expect(values).toContainText("g12_second.txt");
  await shots(page, "conflict");
  await values.getByRole("radio", { name: /2019-05/ }).check();
  await accept.click();
  await expect(rail).toHaveAttribute("data-status", "accepted");
  const employers = (await profile()).sections.experience?.employers as {
    organisation: string;
    start: string;
  }[];
  expect(employers.map((one) => [one.organisation, one.start]).sort()).toEqual([
    ["Example Post", "2016-01"],
    ["Example Rail", "2019-05"],
  ]);
});

test("reject the rest of a section in one click, then reload: every decision is kept", async () => {
  const skills = section("Skills");
  await expect(proposal(YEARS)).toContainText("Calculated");
  await skills
    .getByRole("button", { name: "Reject the rest of this section: Skills" })
    .click();
  await expect(skills.getByRole("status")).toHaveText("Rejected 1.");
  await expect(proposal(YEARS)).toHaveAttribute("data-status", "rejected");
  await expect(page.getByTestId("review-left")).toHaveText(
    "Every proposal is decided.",
  );

  await page.reload();
  await expect(page.getByTestId("proposal")).toHaveCount(9);
  const kept: Record<string, string> = {
    full_name: "accepted",
    headline: "accepted",
    notice_period: "accepted",
    earliest_start: "accepted",
    relocation: "accepted",
    travel: "rejected",
    [POST]: "accepted",
    [RAIL]: "accepted",
    [YEARS]: "rejected",
  };
  for (const [key, status] of Object.entries(kept))
    await expect(proposal(key)).toHaveAttribute("data-status", status);
  await expect(
    page.getByRole("button", { name: /^(Accept|Reject|Edit)/ }),
  ).toHaveCount(0);
  expect(await completeness()).toBeGreaterThan(before);
  await expect(page.getByTestId("review-left")).not.toContainText(DASH);
  for (const name of ["Identity", "Experience", "Skills", "Answers"])
    await expect(section(name)).not.toContainText(DASH);
  await shots(page, "decided");
  expect(errors).toEqual([]);
});
