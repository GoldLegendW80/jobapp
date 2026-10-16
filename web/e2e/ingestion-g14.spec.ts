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
 * Task G14 of the ingestion module against the real stack through Caddy, no mocks, with the
 * fake backend (AGENTS_FAKE_BACKEND=true, never in production): the transcription of images
 * and scanned pages. A new user uploads a screenshot of a CV fragment and a two page scan
 * (both wait for the agent: "will be read by the agent at the next run"), and a short text so
 * that one section holds what a bulk accept may take. The user starts a run through the page,
 * sees the step that reads the page images, and the run ends with everything read. Each
 * document is then Ready with its transcription in the preview. In the review every item cited
 * from a transcribed text carries the "Transcribed, check it" badge and its citation says so,
 * "Accept all in this section" leaves those items out and says how many are left, and the user
 * accepts one of them on its own.
 *
 * Under the fake backend nothing reads a picture: the recorded streams are named after the
 * hash of each uploaded file (api/tests/ingestion/journey_streams.py), the transcription
 * stream stores a made up text and the extraction stream cites it.
 *
 * It sets up its own user, documents and run, and depends on no other journey.
 *
 * Locally, on a scratch Compose project (its own volumes and images, torn down afterwards):
 *   .github/scripts/ci_env.sh 8114 >.env && echo AGENTS_FAKE_BACKEND=true >>.env
 *   docker compose -p jobapp-g14 -f compose.yml -f compose.mac.yml up -d --build --wait
 *   docker compose -p jobapp-g14 -f compose.yml exec -T api alembic upgrade head
 *   E2E_CADDY_URL=http://127.0.0.1:8114 E2E_COMPOSE_PROJECT=jobapp-g14 \
 *     pnpm --dir web exec playwright test -c e2e/ingestion-all.config.ts ingestion-g14
 *   docker compose -p jobapp-g14 -f compose.yml down -v
 */
test.describe.configure({ mode: "serial" });
test.skip(!caddyUrl, "set E2E_CADDY_URL with the Compose stack running");

const stamp = Date.now().toString(36);
const EMAIL = `e2e-ingestion-g14-${stamp}@example.org`;
const NAME = "Sam Example";
const PASSWORD = `ingestion transcription ${stamp} passphrase`;
const tail = () => Math.random().toString(36).slice(2, 12).padEnd(10, "x");
const KEY = `sk-ant-api03-e2egood${tail()}${tail()}Rn42`;

const RUNS = "/api/ingestion/runs";
const CSRF = { "X-Requested-With": "jobapp" };
const FIXTURES = path.join(root, "api/tests/ingestion/fixtures");
const SHOT = "g14_screenshot.png";
const SCAN = "g14_scan.pdf";
const NOTES = "g14_notes.txt";
const FILES = [SHOT, SCAN, NOTES];
const NEXT_RUN = "will be read by the agent at the next run";
const BADGE = "Transcribed, check it";
const HEADLINE = "Timetable analyst for city tram networks";
// One line of what each transcription stream stores, as the preview shows it afterwards.
const SHOT_LINE = "Relocation: ready to move to Lyon or Grenoble";
const SCAN_LINES = [
  "Curriculum vitae of Sam Rivera, page 1 of 2",
  "Travel: up to four days a month",
];
const TRANSCRIBED = ["headline", "relocation", "travel"];
const PARSED = ["notice_period", "earliest_start"];
const DASH = /[–—]| - /;

const shots = makeShots("ingestion-g14", "g14");

let context: BrowserContext;
let page: Page;
let errors: string[];

test.beforeAll(async ({ browser }) => {
  context = await newContext(browser);
  page = await context.newPage();
  errors = watchErrors(page, []);
});

test.afterAll(async () => {
  await context.close();
  clearAttempts([EMAIL]);
});

const row = (name: string) =>
  page
    .getByRole("list", { name: "Your documents" })
    .getByRole("listitem")
    .filter({ hasText: name });
const named = (name: string) => new RegExp(name.replace(".", "\\."));
const state = () => page.getByTestId("run-state");
const proposal = (key: string) =>
  page.locator(`[data-testid="proposal"][data-key="${key}"]`);
const section = (name: string) => page.getByRole("region", { name });

async function openPreview(name: string) {
  await row(name)
    .getByRole("button", { name: `Preview ${name}` })
    .click();
  return page.getByRole("dialog", { name });
}

async function profile() {
  const response = await context.request.get("/api/profile");
  expect(response.status()).toBe(200);
  return (await response.json()) as {
    sections: Record<string, Record<string, unknown>>;
  };
}

test("a screenshot and a two page scan wait for the agent", async () => {
  test.setTimeout(180_000);
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
      mimeType: name.endsWith(".txt")
        ? "text/plain"
        : "application/octet-stream",
      buffer: readFileSync(path.join(FIXTURES, name)),
    })),
  );
  for (const name of [SHOT, SCAN]) {
    await expect(row(name)).toContainText("Waiting for the agent", {
      timeout: 120_000,
    });
    await expect(row(name)).toContainText(NEXT_RUN);
  }
  await expect(row(SHOT)).toContainText("1 page kept as an image");
  await expect(row(SCAN)).toContainText("2 pages kept as images");
  await expect(row(NOTES)).toContainText("Ready");
  await shots(page, "01-waiting-for-the-agent");
});

test("a run started in the page reads the page images, then the texts", async () => {
  test.setTimeout(240_000);
  // The run is started in the Review step. A document that waits for the agent can be ticked.
  await page.goto("/profile/fill?step=review");
  await expect(
    page.getByRole("heading", { name: "Start a run" }),
  ).toBeVisible();
  for (const name of FILES)
    await page.getByRole("checkbox", { name: named(name) }).check();
  await page.getByRole("button", { name: "See the estimate" }).click();
  const dialog = page.getByRole("dialog", { name: "Before the run starts" });
  await expect(dialog.getByTestId("estimate")).toBeVisible();
  await expect(dialog).not.toContainText(DASH);
  await dialog.getByRole("checkbox", { name: /I have read this/ }).check();
  await dialog.getByRole("button", { name: "Start the run" }).click();
  await expect(dialog).toBeHidden();
  await expect(
    page.getByRole("heading", { name: "The run in progress" }),
  ).toBeVisible();

  // The transcription step of the scan, shown while it runs (its stream waits between calls).
  await expect(page.getByTestId("run-current")).toContainText(
    /g14_scan\.pdf\. Reading its page images\./,
    { timeout: 100_000 },
  );
  await shots(page, "02-reading-the-page-images");

  await expect(state()).toHaveAttribute("data-state", "done", {
    timeout: 150_000,
  });
  await expect(state()).toContainText(
    "The run is finished. Everything you selected was read.",
  );
  // The summary counts documents: the three, two of them read through their page images.
  await expect(page.getByTestId("run-figures")).toContainText("3 of 3 read");
  const listed = (await (await context.request.get(RUNS)).json()) as {
    runs: { id: string }[];
  };
  const run = (await (
    await context.request.get(`${RUNS}/${listed.runs[0]?.id ?? ""}`)
  ).json()) as {
    status: string;
    partial: boolean;
    counts: { proposals: number };
  };
  expect([run.status, run.partial]).toEqual(["done", false]);
  // A headline and an answer from the screenshot, an answer from the scan, two from the text.
  expect(run.counts.proposals).toBe(5);
  await shots(page, "03-run-done");
});

test("each document is ready, with its transcription in the preview", async () => {
  await page.goto("/profile/fill?step=documents");
  for (const name of [SHOT, SCAN]) {
    await expect(row(name)).toContainText("Ready");
    await expect(row(name)).not.toContainText(NEXT_RUN);
    await expect(row(name)).not.toContainText("Waiting for the agent");
  }

  const scan = await openPreview(SCAN);
  for (const line of SCAN_LINES) await expect(scan).toContainText(line);
  await expect(scan).not.toContainText(
    "The agent will read it at the next run.",
  );
  // The page images are deleted once their text is stored.
  await expect(scan.getByRole("img")).toHaveCount(0);
  await shots(page, "04-preview-transcribed-scan", false);
  await page.keyboard.press("Escape");
  await expect(page.getByRole("dialog")).toHaveCount(0);

  const shot = await openPreview(SHOT);
  await expect(shot).toContainText(SHOT_LINE);
  await expect(shot.getByRole("img")).toHaveCount(0);
  await page.keyboard.press("Escape");
  await expect(page.getByRole("dialog")).toHaveCount(0);
});

test("an item cited from a transcription carries the badge, and a bulk accept leaves it out", async () => {
  await page.goto("/profile/fill?step=review");
  await expect(page.getByTestId("proposal")).toHaveCount(5);
  for (const key of TRANSCRIBED) {
    await expect(proposal(key)).toContainText(BADGE);
    await expect(proposal(key)).toContainText("(transcribed, check it)");
  }
  for (const key of PARSED) {
    await expect(proposal(key)).not.toContainText(BADGE);
    await expect(proposal(key)).not.toContainText("transcribed");
  }
  await expect(proposal("travel")).toContainText(SCAN);
  await expect(proposal("relocation")).toContainText(SHOT);
  await shots(page, "05-review-transcribed-badges");

  // A section that holds nothing else offers no bulk accept, and says why.
  // The innermost region that holds the headline: its section, not the review around it.
  const identity = page
    .getByRole("region")
    .filter({ has: proposal("headline") })
    .last();
  await expect(
    identity.getByRole("button", { name: /^Accept all in this section/ }),
  ).toHaveCount(0);
  await expect(identity).toContainText(
    "What is left here is accepted one by one.",
  );

  // Two answers come from a text and two from a transcription: the bulk accept takes the
  // first two, leaves the transcribed ones pending, and says so.
  const answers = section("Answers");
  await answers
    .getByRole("button", { name: "Accept all in this section: Answers" })
    .click();
  await expect(answers.getByRole("status")).toHaveText(
    "Accepted 2. 2 left for you to accept one by one.",
  );
  for (const key of PARSED)
    await expect(proposal(key)).toHaveAttribute("data-status", "accepted");
  for (const key of TRANSCRIBED) {
    await expect(proposal(key)).toHaveAttribute("data-status", "pending");
    await expect(proposal(key)).toContainText(BADGE);
  }
  const held = await profile();
  expect(held.sections.answers).toMatchObject({
    notice_period: "Six weeks",
    earliest_start: "The first of June",
  });
  expect(held.sections.answers?.relocation ?? null).toBeNull();
  expect(held.sections.answers?.travel ?? null).toBeNull();
  await shots(page, "06-bulk-accept-left-them-out");
});

test("the user checks a transcribed item and accepts it on its own", async () => {
  const headline = proposal("headline");
  await headline.getByRole("button", { name: "Accept: Headline" }).click();
  await expect(headline).toHaveAttribute("data-status", "accepted");
  const travel = proposal("travel");
  await travel.getByRole("button", { name: /^Accept: / }).click();
  await expect(travel).toHaveAttribute("data-status", "accepted");
  await expect(proposal("relocation")).toHaveAttribute(
    "data-status",
    "pending",
  );
  const held = await profile();
  expect(held.sections.identity?.headline).toBe(HEADLINE);
  expect(held.sections.answers?.travel).toBe("Up to four days a month");
  await shots(page, "07-accepted-one-by-one");

  await expect(section("Answers")).not.toContainText(DASH);
  expect(errors).toEqual([]);
});
