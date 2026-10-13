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
 * Task G11 of the ingestion module against the real stack through Caddy, no mocks, with the
 * fake backend (AGENTS_FAKE_BACKEND=true, never in production): the run of /profile/fill by
 * clicks. A new user with a Claude subscription adds four documents, opens the start dialog on
 * two of them, reads the estimate, the ceiling and the consent text exactly as the server gave
 * them, cannot start until the box is ticked, starts (the request carries the `quote_id` of
 * the estimate shown), reloads mid run, watches the progress and reads the summary; then runs
 * the fixture that reaches its ceiling, reads what was spent and what a Continue adds, reloads
 * and opens a second tab mid pause, clicks Continue (the request carries the figure shown) and
 * sees the run finish; opens the dialog again and sees the one line reminder and the documents
 * marked as reused; a second user with an API key sees the amounts and the other model's name.
 *
 * The documents and their recorded streams are the ones of ingestion-g10.spec.ts (see there
 * for the hashes and why g10_large.txt pauses its run on the ceiling).
 *
 * Locally, on a scratch Compose project (its own volumes and images, torn down afterwards):
 *   .github/scripts/ci_env.sh 8111 >.env && echo AGENTS_FAKE_BACKEND=true >>.env
 *   docker compose -p jobapp-g11 -f compose.yml -f compose.mac.yml up -d --build --wait
 *   docker compose -p jobapp-g11 -f compose.yml exec -T api alembic upgrade head
 *   E2E_CADDY_URL=http://127.0.0.1:8111 E2E_COMPOSE_PROJECT=jobapp-g11 \
 *     pnpm --dir web exec playwright test -c e2e/ingestion-all.config.ts ingestion-g11
 *   docker compose -p jobapp-g11 -f compose.yml down -v
 */
test.describe.configure({ mode: "serial" });
test.skip(!caddyUrl, "set E2E_CADDY_URL with the Compose stack running");

const stamp = Date.now().toString(36);
const EMAIL = `e2e-ingestion-g11-${stamp}@example.org`;
const KEY_EMAIL = `e2e-ingestion-g11-key-${stamp}@example.org`;
const NAME = "Alex Example";
const PASSWORD = `ingestion run page ${stamp} passphrase`;
const tail = () => Math.random().toString(36).slice(2, 12).padEnd(10, "x");
const TOKEN = `sk-ant-oat01-e2egood${tail()}${tail()}Sub1`;
const KEY = `sk-ant-api03-e2egood${tail()}${tail()}Rn42`;

const RUNS = "/api/ingestion/runs";
const ESTIMATE = "/api/ingestion/runs/estimate";
const CSRF = { "X-Requested-With": "jobapp" };
const DASH = /[–—]| - /;

const FIXTURES = path.join(root, "api/tests/ingestion/fixtures");
const FIRST = "g10_first.txt";
const SECOND = "g10_second.txt";
const LARGE = "g10_large.txt";
const PLAIN = "g10_plain.txt";
const ALL = [FIRST, SECOND, LARGE, PLAIN];
const FIRST_TOKENS = 3_760;
const SECOND_TOKENS = 2_820;
const LARGE_TOKENS = 380_000;

const shots = makeShots("ingestion-g11", "g11");
const figure = (value: number) => value.toLocaleString("en-US");
const named = (name: string) => new RegExp(name.replace(".", "\\."));

interface Estimate {
  documents: { id: string; name: string; reused_steps: number }[];
  model: string | null;
  cost_note: string | null;
  tokens: { low: number; high: number; ceiling: number };
  cost: {
    currency: string;
    low: string;
    high: string;
    ceiling: string;
    prices_as_of: string;
    note: string;
  } | null;
  quote_id: string;
}

interface Run {
  id: string;
  status: string;
  partial: boolean;
  tokens: {
    spent: number;
    ceiling: number;
    continue_adds: number | null;
    history: { from: number; to: number }[];
  };
  counts: { items: number; done: number; steps: number };
}

let context: BrowserContext;
let page: Page;
let errors: string[];
let subscriptionModel: string | null = null;

test.beforeAll(async ({ browser }) => {
  context = await newContext(browser);
  page = await context.newPage();
  errors = watchErrors(page, []);
});

test.afterAll(async () => {
  await context.close();
  clearAttempts([EMAIL, KEY_EMAIL]);
});

async function signUp(
  to: Page,
  email: string,
  kind: string,
  secret: string,
  files: string[],
) {
  await to.goto(cliLink(email));
  await setPassword(to, PASSWORD, NAME);
  const saved = await to.request.put("/api/me/model-credential", {
    headers: CSRF,
    data: { kind, secret },
  });
  expect(saved.status()).toBe(200);
  await to.goto("/profile/fill?step=documents");
  await to.getByTestId("document-files").setInputFiles(
    files.map((name) => ({
      name,
      mimeType: "text/plain",
      buffer: readFileSync(path.join(FIXTURES, name)),
    })),
  );
  const rows = to
    .getByRole("list", { name: "Your documents" })
    .getByRole("listitem");
  for (const name of files)
    await expect(rows.filter({ hasText: name })).toContainText("Ready");
  await to
    .getByRole("navigation", { name: "Steps" })
    .getByRole("button", { name: /Review/ })
    .click();
  await expect(
    to.getByRole("heading", { level: 2, name: /Step 3: Review/ }),
  ).toBeVisible();
}

/** Ticks exactly `wanted` and opens the dialog; answers the estimate the page was given. */
async function openDialog(
  on: Page,
  all: string[],
  wanted: string[],
): Promise<Estimate> {
  // The run reads the documents in the order they were ticked.
  for (const name of all)
    await on.getByRole("checkbox", { name: named(name) }).uncheck();
  for (const name of wanted)
    await on.getByRole("checkbox", { name: named(name) }).check();
  const [answer] = await Promise.all([
    on.waitForResponse(
      (response) =>
        new URL(response.url()).pathname === ESTIMATE &&
        response.request().method() === "POST",
    ),
    on.getByRole("button", { name: "See the estimate" }).click(),
  ]);
  expect(answer.status()).toBe(200);
  await expect(dialogOf(on).getByTestId("estimate")).toBeVisible();
  return (await answer.json()) as Estimate;
}

const dialogOf = (on: Page) =>
  on.getByRole("dialog", { name: "Before the run starts" });
const state = () => page.getByTestId("run-state");

/** Clicks Start and answers the body the page sent to the start route. */
async function clickStart(): Promise<Record<string, unknown>> {
  const [request] = await Promise.all([
    page.waitForRequest(
      (one) => new URL(one.url()).pathname === RUNS && one.method() === "POST",
    ),
    dialogOf(page).getByRole("button", { name: "Start the run" }).click(),
  ]);
  return request.postDataJSON() as Record<string, unknown>;
}

async function activeRun(): Promise<Run> {
  const listed = await context.request.get(RUNS);
  expect(listed.status()).toBe(200);
  const list = (await listed.json()) as {
    runs: { id: string }[];
    active_run_id: string | null;
  };
  const id = list.active_run_id ?? list.runs[0]?.id;
  if (id === undefined) throw new Error("the user has no run");
  const response = await context.request.get(`${RUNS}/${id}`);
  expect(response.status()).toBe(200);
  return (await response.json()) as Run;
}

test("a new user with a subscription adds documents and opens the review step", async () => {
  await signUp(page, EMAIL, "claude_subscription", TOKEN, ALL);
  await expect(
    page.getByRole("heading", { name: "Start a run" }),
  ).toBeVisible();
  // Nothing is chosen for the user, and nothing can be asked for without a choice.
  await expect(
    page.getByRole("button", { name: "See the estimate" }),
  ).toBeDisabled();
  await expect(page.locator("main")).not.toContainText(DASH);
});

test("the start dialog shows the estimate and the consent, and starts only on a tick and a click", async () => {
  const estimate = await openDialog(page, ALL, [FIRST, SECOND]);
  subscriptionModel = estimate.model;
  const dialog = dialogOf(page);
  await expect(dialog.getByTestId("estimate-range")).toHaveText(
    `About ${figure(estimate.tokens.low)} to ${figure(estimate.tokens.high)} tokens.`,
  );
  await expect(dialog.getByTestId("estimate-ceiling")).toHaveText(
    `The run pauses at ${figure(estimate.tokens.ceiling)} tokens, and asks you before going on.`,
  );
  expect(estimate.model).not.toBeNull();
  await expect(dialog.getByTestId("estimate-model")).toContainText(
    estimate.model ?? "",
  );
  // A subscription: tokens only, and the sentence on the quota.
  expect(estimate.cost).toBeNull();
  await expect(dialog.getByTestId("estimate-quota")).toHaveText(
    estimate.cost_note ?? "",
  );
  await expect(dialog).not.toContainText("USD");
  await expect(dialog).not.toContainText(DASH);

  const consent = (await (
    await context.request.get("/api/ingestion/consent")
  ).json()) as { text: string; accepted: boolean };
  expect(consent.accepted).toBe(false);
  await expect(dialog.getByTestId("consent-text")).toHaveText(consent.text);
  const box = dialog.getByRole("checkbox", { name: /I have read this/ });
  await expect(box).not.toBeChecked();
  const start = dialog.getByRole("button", { name: "Start the run" });
  await expect(start).toBeDisabled();
  await shots(page, "start-dialog-consent");

  await box.check();
  await expect(start).toBeEnabled();
  // Still nothing recorded and nothing started.
  expect(
    ((await (await context.request.get(RUNS)).json()) as { runs: unknown[] })
      .runs,
  ).toEqual([]);

  const sent = await clickStart();
  expect(sent.quote_id).toBe(estimate.quote_id);
  expect(sent.document_ids).toEqual(estimate.documents.map((one) => one.id));
  expect(sent.reread_document_ids).toEqual([]);
  await expect(dialog).toBeHidden();
  await expect(
    page.getByRole("heading", { name: "The run in progress" }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "See the estimate" }),
  ).toHaveCount(0);
});

test("the run survives a reload, moves through both documents and ends on its summary", async () => {
  // Nothing of the run lives in the page: after a reload it is found again and still shown.
  await page.reload();
  await expect(state()).toBeVisible();
  await expect(page.getByTestId("run-current")).toContainText(
    /Document [12] of 2: g10_second\.txt\. Reading its text\./,
    { timeout: 100_000 },
  );
  await expect(
    page.getByRole("button", { name: "Cancel the run" }),
  ).toBeEnabled();

  await expect(state()).toHaveAttribute("data-state", "done", {
    timeout: 100_000,
  });
  await expect(state()).toContainText(
    "The run is finished. Everything you selected was read.",
  );
  const run = await activeRun();
  expect(run.status).toBe("done");
  expect(run.partial).toBe(false);
  expect(run.tokens.spent).toBe(FIRST_TOKENS + SECOND_TOKENS);
  const figures = page.getByTestId("run-figures");
  await expect(figures).toContainText("2 of 2 read");
  await expect(figures).toContainText(`${figure(run.tokens.spent)} used`);
  await expect(figures).toContainText(
    `${figure(run.counts.items)} ${run.counts.items === 1 ? "item" : "items"}`,
  );
  await expect(
    page.getByRole("heading", { name: "Your last run" }),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "Start another run" }),
  ).toBeVisible();
  await expect(page.locator("main")).not.toContainText(DASH);
  await expect(page.locator("main")).not.toContainText(
    /token_ceiling|\bslots\b/,
  );
  await shots(page, "summary");
});

test("a run that reaches its ceiling shows what was spent, and goes on after a Continue with the figure shown", async () => {
  const estimate = await openDialog(page, ALL, [LARGE, PLAIN]);
  const dialog = dialogOf(page);
  // The consent is recorded: one line, no box.
  await expect(dialog.getByTestId("reminder")).toBeVisible();
  await expect(
    dialog.getByRole("checkbox", { name: /I have read/ }),
  ).toHaveCount(0);
  const sent = await clickStart();
  expect(sent.quote_id).toBe(estimate.quote_id);
  // The large document first: its step ends over the ceiling, and one step is left to run.
  expect(estimate.documents.map((one) => one.name)).toEqual([LARGE, PLAIN]);
  expect(sent.document_ids).toEqual(estimate.documents.map((one) => one.id));

  await expect(state()).toHaveAttribute("data-state", "ceiling", {
    timeout: 100_000,
  });
  const paused = await activeRun();
  expect(paused.status).toBe("paused");
  expect(paused.tokens.spent).toBeGreaterThanOrEqual(LARGE_TOKENS);
  const adds = paused.tokens.continue_adds;
  if (adds === null || adds <= 0)
    throw new Error("the paused run names no figure for a Continue");
  await expect(state()).toContainText(
    "The run has paused at its token ceiling.",
  );
  await expect(page.getByTestId("ceiling-spent")).toHaveText(
    `It has used ${figure(paused.tokens.spent)} tokens, and its ceiling is ${figure(paused.tokens.ceiling)} tokens.`,
  );
  await expect(page.getByTestId("continue-adds")).toContainText(
    `Continue raises the ceiling by ${figure(adds)} tokens`,
  );
  const button = `Continue with ${figure(adds)} more tokens`;
  await expect(page.getByRole("button", { name: button })).toBeEnabled();
  await expect(
    page.getByRole("button", { name: "Cancel the run" }),
  ).toBeEnabled();
  await expect(page.getByRole("button", { name: /Resume/ })).toHaveCount(0);
  await expect(page.locator("main")).not.toContainText(DASH);
  await shots(page, "paused-on-ceiling");

  // Mid pause, a reload and a second tab show the same pause with the same figure.
  await page.reload();
  await expect(page.getByRole("button", { name: button })).toBeEnabled();
  const tab = await context.newPage();
  await tab.goto("/profile/fill?step=review");
  await expect(tab.getByRole("button", { name: button })).toBeEnabled();
  await tab.close();
  expect((await activeRun()).tokens.history).toEqual([]);

  const [request] = await Promise.all([
    page.waitForRequest(
      (one) =>
        new URL(one.url()).pathname === `${RUNS}/${paused.id}/continue` &&
        one.method() === "POST",
    ),
    page.getByRole("button", { name: button }).click(),
  ]);
  expect(request.postDataJSON()).toEqual({ adds });

  await expect(state()).toHaveAttribute("data-state", "done", {
    timeout: 100_000,
  });
  const done = await activeRun();
  expect(done.id).toBe(paused.id);
  expect(done.tokens.history).toHaveLength(1);
  expect(done.tokens.history[0]?.from).toBe(paused.tokens.ceiling);
  expect(done.tokens.history[0]?.to).toBe(done.tokens.ceiling);
  await expect(page.getByTestId("run-figures")).toContainText(
    `${figure(done.tokens.spent)} used`,
  );
});

test("the dialog opened again shows the reminder and the documents as reused, and starts nothing", async () => {
  const estimate = await openDialog(page, ALL, [FIRST, SECOND]);
  const dialog = dialogOf(page);
  expect(estimate.documents.map((one) => one.reused_steps)).toEqual([1, 1]);
  await expect(dialog.getByTestId("reminder")).toBeVisible();
  await expect(dialog.getByTestId("consent-text")).toHaveCount(0);
  const rows = dialog
    .getByRole("region", { name: "What is read" })
    .getByRole("listitem");
  for (const name of [FIRST, SECOND])
    await expect(rows.filter({ hasText: name })).toContainText(
      "already read and reused",
    );
  await expect(
    dialog.getByRole("checkbox", { name: /Read g10_first\.txt again/ }),
  ).not.toBeChecked();
  await expect(dialog).toContainText("Reading it again uses tokens again.");
  await shots(page, "start-dialog-reused");

  const before = (await activeRun()).id;
  await dialog.getByRole("button", { name: "Not now" }).click();
  await expect(dialog).toBeHidden();
  await expect(
    page.getByRole("button", { name: "See the estimate" }),
  ).toBeFocused();
  expect((await activeRun()).id).toBe(before);
  expect(errors).toEqual([]);
});

test("a second user with an API key sees the amounts as estimates and the other model's name", async ({
  browser,
}) => {
  const other = await newContext(browser);
  try {
    const mine = await other.newPage();
    const seen = watchErrors(mine, []);
    await signUp(mine, KEY_EMAIL, "anthropic_api_key", KEY, [FIRST]);
    // Another user's runs are not hers: the page opens on a first start.
    await expect(
      mine.getByRole("heading", { name: "Start a run" }),
    ).toBeVisible();
    const estimate = await openDialog(mine, [FIRST], [FIRST]);
    const dialog = dialogOf(mine);
    const cost = estimate.cost;
    if (cost === null) throw new Error("an API key has amounts");
    await expect(dialog.getByTestId("estimate-cost")).toHaveText(
      `About ${cost.low} to ${cost.high} ${cost.currency}, as an estimate.`,
    );
    await expect(dialog.getByTestId("estimate-cost-ceiling")).toHaveText(
      `The pause at ${figure(estimate.tokens.ceiling)} tokens is estimated at about ${cost.ceiling} ${cost.currency}. It is an estimate, not a limit on what you pay.`,
    );
    await expect(dialog).toContainText(cost.note);
    expect(cost.note).toContain(cost.prices_as_of);
    expect(estimate.model).not.toBeNull();
    expect(estimate.model).not.toBe(subscriptionModel);
    await expect(dialog.getByTestId("estimate-model")).toContainText(
      estimate.model ?? "",
    );
    await expect(dialog.getByTestId("estimate-quota")).toHaveCount(0);
    await expect(dialog).not.toContainText(/at most|maximum/i);
    await expect(dialog).not.toContainText(DASH);
    await shots(mine, "start-dialog-api-key");
    await dialog.getByRole("button", { name: "Not now" }).click();
    expect(
      ((await (await other.request.get(RUNS)).json()) as { runs: unknown[] })
        .runs,
    ).toEqual([]);
    expect(seen).toEqual([]);
  } finally {
    await other.close();
  }
});
