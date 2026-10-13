import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import path from "node:path";

import { expect, test, type BrowserContext, type Page } from "@playwright/test";

import {
  caddyUrl,
  clearAttempts,
  cliLink,
  newContext,
  root,
  setPassword,
  watchErrors,
} from "./stack";

/**
 * Task G10 of the ingestion module against the real stack through Caddy, no mocks, with the
 * fake backend (AGENTS_FAKE_BACKEND=true, never in production) and the browser's session (the
 * page of a run is G11's): a new user saves a credential, adds two documents on the documents
 * step, is refused a start without the consent, gives it, starts, and watches the run go
 * through both steps to `done`; a second run over the same documents reuses both steps; a run
 * whose first step reports a large usage pauses on its ceiling between its two steps and
 * finishes after one Continue; a run that reads a document again is cancelled while its step
 * runs; a second start is refused while a run is active; another user sees none of it.
 *
 * The documents are the made up texts of api/tests/ingestion/fixtures/g10_*.txt (a fictitious
 * person), uploaded byte for byte, so their hashes are fixed. A step replays the stream named
 * after the first 12 hexadecimal digits of its document's sha256 (api/tests/ingestion/
 * journey_streams.py writes them, test_journey_streams.py checks them):
 *   g10_first.txt   0cc250af0492  three items, 3,760 tokens
 *   g10_second.txt  eebaa7372b85  one item, 2,820 tokens, two seconds before each message
 *   g10_large.txt   37736dd6660b  one item, 380,000 tokens, nearly all in its last message
 *   g10_plain.txt   0ee52d5e117a  no stream of its own: the default one, 1,880 tokens, no item
 * The large step: with `H` the high estimate of a run over g10_large.txt and g10_plain.txt, its
 * 380,000 tokens are over the ceiling `2 * H` and under `3 * H` minus the low estimate of the
 * second step. Its usage comes with its last message, so the step ends done, the run pauses
 * before the second step, and one Continue (`H` more) is enough for it.
 *
 * Locally, on a scratch Compose project (its own volumes and images, torn down afterwards):
 *   .github/scripts/ci_env.sh 8110 >.env && echo AGENTS_FAKE_BACKEND=true >>.env
 *   docker compose -p jobapp-g10 -f compose.yml -f compose.mac.yml up -d --build --wait
 *   docker compose -p jobapp-g10 -f compose.yml exec -T api alembic upgrade head
 *   E2E_CADDY_URL=http://127.0.0.1:8110 E2E_COMPOSE_PROJECT=jobapp-g10 \
 *     pnpm --dir web exec playwright test -c e2e/ingestion-all.config.ts ingestion-g10
 *   docker compose -p jobapp-g10 -f compose.yml down -v
 */
test.describe.configure({ mode: "serial" });
test.skip(!caddyUrl, "set E2E_CADDY_URL with the Compose stack running");

const stamp = Date.now().toString(36);
const EMAIL = `e2e-ingestion-g10-${stamp}@example.org`;
const OTHER_EMAIL = `e2e-ingestion-g10-other-${stamp}@example.org`;
const NAME = "Alex Example";
const PASSWORD = `ingestion chain ${stamp} passphrase`;
const tail = () => Math.random().toString(36).slice(2, 12).padEnd(10, "x");
const KEY = `sk-ant-api03-e2egood${tail()}${tail()}Rn42`;

const DOCUMENTS = "/api/ingestion/documents";
const ESTIMATE = "/api/ingestion/runs/estimate";
const CONSENT = "/api/ingestion/consent";
const RUNS = "/api/ingestion/runs";
const CSRF = { "X-Requested-With": "jobapp" };

const FIXTURES = path.join(root, "api/tests/ingestion/fixtures");
const FIRST = "g10_first.txt";
const SECOND = "g10_second.txt";
const LARGE = "g10_large.txt";
const PLAIN = "g10_plain.txt";
const HASHES: Record<string, string> = {
  [FIRST]: "0cc250af0492",
  [SECOND]: "eebaa7372b85",
  [LARGE]: "37736dd6660b",
  [PLAIN]: "0ee52d5e117a",
};
const FIRST_TOKENS = 3_760;
const SECOND_TOKENS = 2_820;
const LARGE_TOKENS = 380_000;
const DEFAULT_TOKENS = 1_880;
const ACTIVE = ["running", "paused"];

const bytes = (name: string) => readFileSync(path.join(FIXTURES, name));

interface Estimate {
  steps: number;
  reused_steps: number;
  credential_kind: string | null;
  tokens: { low: number; high: number; ceiling: number };
  consent: { version: number; accepted: boolean };
  can_start: boolean;
  quote_id: string;
}

interface Step {
  position: number;
  kind: string;
  document_id: string;
  document_name: string | null;
  status: string;
  attempt: number;
  items: number;
  refused: number;
  agent_run_id: string | null;
  tokens: number | null;
  cost_usd: string | null;
  error: string | null;
}

interface Run {
  id: string;
  status: string;
  partial: boolean;
  paused_reason: string | null;
  waiting: string | null;
  cancel_requested: boolean;
  document_ids: string[];
  current_agent_run_id: string | null;
  tokens: {
    spent: number;
    ceiling: number;
    low: number;
    high: number;
    history: { at: string; spent: number; from: number; to: number }[];
    overshoot_requests: number;
    overshoot: string;
  };
  counts: {
    steps: number;
    planned: number;
    running: number;
    done: number;
    reused: number;
    failed: number;
    cancelled: number;
    items: number;
    refused: number;
    proposals: number;
  };
  steps: Step[];
  finished_at: string | null;
}

interface RunList {
  runs: { id: string; status: string }[];
  active_run_id: string | null;
}

let context: BrowserContext;
let page: Page;
let errors: string[];
const ids: Record<string, string> = {};
const runs: string[] = [];

test.beforeAll(async ({ browser }) => {
  context = await newContext(browser);
  page = await context.newPage();
  errors = watchErrors(page, []);
});

test.afterAll(async () => {
  await context.close();
  clearAttempts([EMAIL, OTHER_EMAIL]);
});

async function estimateOf(
  documents: string[],
  reread: string[] = [],
): Promise<Estimate> {
  const response = await context.request.post(ESTIMATE, {
    headers: CSRF,
    data: { document_ids: documents, reread_document_ids: reread },
  });
  expect(response.status()).toBe(200);
  return (await response.json()) as Estimate;
}

function start(quote: Estimate, documents: string[], reread: string[] = []) {
  return context.request.post(RUNS, {
    headers: CSRF,
    data: {
      document_ids: documents,
      reread_document_ids: reread,
      quote_id: quote.quote_id,
    },
  });
}

async function started(
  quote: Estimate,
  documents: string[],
  reread: string[] = [],
): Promise<Run> {
  const response = await start(quote, documents, reread);
  expect(response.status()).toBe(201);
  const run = (await response.json()) as Run;
  runs.push(run.id);
  return run;
}

async function read(id: string): Promise<Run> {
  const response = await context.request.get(`${RUNS}/${id}`);
  expect(response.status()).toBe(200);
  expect(response.headers()["cache-control"]).toBe("no-store");
  return (await response.json()) as Run;
}

/** Polls the run as its page will, until `wanted` holds. The chain moves by the hook of each
 * step, and by the cron every minute when a hook was missed. */
async function until(
  id: string,
  what: string,
  wanted: (run: Run) => boolean,
): Promise<Run> {
  let last = await read(id);
  await expect
    .poll(
      async () => {
        last = await read(id);
        return wanted(last);
      },
      { message: what, timeout: 100_000, intervals: [250, 500, 1_000] },
    )
    .toBe(true);
  return last;
}

const act = (id: string, action: string, as: BrowserContext = context) =>
  as.request.post(`${RUNS}/${id}/${action}`, { headers: CSRF });

test("the fixtures are the ones the recorded streams are named after", () => {
  for (const [name, hash] of Object.entries(HASHES))
    expect(
      createHash("sha256").update(bytes(name)).digest("hex").slice(0, 12),
    ).toBe(hash);
});

test("a new user saves a credential and adds two documents on the documents step", async () => {
  await page.goto(cliLink(EMAIL));
  await setPassword(page, PASSWORD, NAME);
  const saved = await context.request.put("/api/me/model-credential", {
    headers: CSRF,
    data: { kind: "anthropic_api_key", secret: KEY },
  });
  expect(saved.status()).toBe(200);

  await page.goto("/profile/fill?step=documents");
  await page.getByTestId("document-files").setInputFiles(
    [FIRST, SECOND].map((name) => ({
      name,
      mimeType: "text/plain",
      buffer: bytes(name),
    })),
  );
  const rows = page
    .getByRole("list", { name: "Your documents" })
    .getByRole("listitem");
  for (const name of [FIRST, SECOND])
    await expect(rows.filter({ hasText: name })).toContainText("Ready");
  await expect(page.locator("main")).not.toContainText(/[–—]| - /);

  const listed = await context.request.get(DOCUMENTS);
  expect(listed.status()).toBe(200);
  for (const one of (
    (await listed.json()) as { items: { id: string; name: string }[] }
  ).items)
    ids[one.name] = one.id;
  expect(Object.keys(ids).sort()).toEqual([FIRST, SECOND]);
});

test("a start is refused without the consent, then the run goes through both steps to done", async () => {
  const documents = [ids[FIRST], ids[SECOND]];
  const before = await estimateOf(documents);
  expect(before.steps).toBe(2);
  expect(before.reused_steps).toBe(0);
  expect(before.credential_kind).toBe("anthropic_api_key");
  expect(before.consent.accepted).toBe(false);

  const refused = await start(before, documents);
  expect(refused.status()).toBe(409);
  expect(((await refused.json()) as { code: string }).code).toBe(
    "consent_required",
  );
  expect(
    ((await (await context.request.get(RUNS)).json()) as RunList).runs,
  ).toEqual([]);

  const given = await context.request.post(CONSENT, {
    headers: CSRF,
    data: { version: before.consent.version },
  });
  expect(given.status()).toBe(200);
  const quote = await estimateOf(documents);
  expect(quote.can_start).toBe(true);

  const run = await started(quote, documents);
  expect(ACTIVE).toContain(run.status);
  expect([...run.document_ids].sort()).toEqual([...documents].sort());
  expect(run.tokens.low).toBe(quote.tokens.low);
  expect(run.tokens.high).toBe(quote.tokens.high);
  expect(run.tokens.ceiling).toBe(2 * quote.tokens.high);
  expect(run.tokens.history).toEqual([]);

  // One run at a time: a second start names the run in progress.
  const second = await start(quote, documents);
  expect(second.status()).toBe(409);
  expect(await second.json()).toMatchObject({
    code: "active_run",
    run_id: run.id,
  });
  const list = (await (await context.request.get(RUNS)).json()) as RunList;
  expect(list.active_run_id).toBe(run.id);
  expect(list.runs.map((one) => one.id)).toEqual([run.id]);

  const seen = new Set<string>();
  const done = await until(run.id, "the run ends", (now) => {
    for (const step of now.steps) seen.add(`${step.position} ${step.status}`);
    return !ACTIVE.includes(now.status);
  });
  expect(done.status).toBe("done");
  expect(done.partial).toBe(false);
  expect(done.finished_at).not.toBeNull();
  expect(done.current_agent_run_id).toBeNull();
  // The second step waits two seconds before each of its messages: it was seen running.
  expect([...seen]).toContain(`${done.steps[1].position} running`);
  expect(
    done.steps.map((step) => [step.document_name, step.kind, step.status]),
  ).toEqual([
    [FIRST, "extract", "done"],
    [SECOND, "extract", "done"],
  ]);
  expect(
    done.steps.map((step) => [step.items, step.refused, step.tokens]),
  ).toEqual([
    [3, 0, FIRST_TOKENS],
    [1, 0, SECOND_TOKENS],
  ]);
  expect(done.steps.every((step) => step.error === null)).toBe(true);
  // An Anthropic API key: each step states its cost.
  for (const step of done.steps) expect(step.cost_usd).toMatch(/^0\.02/);
  expect(done.counts).toMatchObject({
    steps: 2,
    planned: 0,
    running: 0,
    done: 2,
    reused: 0,
    failed: 0,
    cancelled: 0,
    items: 4,
    refused: 0,
    proposals: 0,
  });
  // The tokens spent against the ceiling, and the measured overshoot for the page to state.
  expect(done.tokens.spent).toBe(FIRST_TOKENS + SECOND_TOKENS);
  expect(done.tokens.spent).toBeLessThan(done.tokens.ceiling);
  expect(done.tokens.overshoot_requests).toBe(2);
  expect(done.tokens.overshoot).toMatch(/\w/);
  expect(done.tokens.overshoot).not.toMatch(/[–—]| - /);
});

test("a second run over the same documents reuses both steps and calls no model", async () => {
  const documents = [ids[FIRST], ids[SECOND]];
  const quote = await estimateOf(documents);
  expect(quote.reused_steps).toBe(2);
  const run = await started(quote, documents);
  const done = await until(
    run.id,
    "the reused run ends",
    (now) => !ACTIVE.includes(now.status),
  );
  expect(done.status).toBe("done");
  expect(done.counts).toMatchObject({ steps: 2, reused: 2, items: 4 });
  expect(done.steps.map((step) => step.agent_run_id)).toEqual([null, null]);
  expect(done.tokens.spent).toBe(0);
});

test("a run pauses on its ceiling between two steps and one Continue finishes it", async () => {
  for (const name of [LARGE, PLAIN]) {
    const stored = await context.request.post(DOCUMENTS, {
      headers: { ...CSRF, "X-Filename": `UTF-8''${name}` },
      data: bytes(name),
    });
    expect(stored.status()).toBe(201);
    ids[name] = ((await stored.json()) as { id: string }).id;
  }
  const documents = [ids[LARGE], ids[PLAIN]];
  const quote = await estimateOf(documents);
  const high = quote.tokens.high;
  expect(quote.tokens.ceiling).toBe(2 * high);
  // The header's arithmetic: over the ceiling, and one more `H` leaves room for the next step.
  expect(LARGE_TOKENS).toBeGreaterThanOrEqual(2 * high);
  expect(LARGE_TOKENS).toBeLessThan(3 * high - quote.tokens.low);

  const run = await started(quote, documents);
  const paused = await until(
    run.id,
    "the run pauses",
    (now) => now.status !== "running",
  );
  expect(paused.status).toBe("paused");
  expect(paused.paused_reason).toBe("token_ceiling");
  expect(paused.tokens.spent).toBe(LARGE_TOKENS);
  expect(paused.tokens.spent).toBeGreaterThanOrEqual(paused.tokens.ceiling);
  expect(paused.steps.map((step) => [step.status, step.items])).toEqual([
    ["done", 1],
    ["planned", 0],
  ]);
  expect(paused.current_agent_run_id).toBeNull();

  // A paused run is still the user's one run, and Resume is not the answer to a ceiling.
  // The estimate of now counts the finished step as reused, so the echo of the first one is
  // refused as changed, and a start with the fresh one names the run in progress.
  const stale = await start(quote, documents);
  expect(stale.status()).toBe(409);
  expect(((await stale.json()) as { code: string }).code).toBe(
    "estimate_changed",
  );
  const fresh = await estimateOf(documents);
  expect(fresh.reused_steps).toBe(1);
  const second = await start(fresh, documents);
  expect(second.status()).toBe(409);
  expect(await second.json()).toMatchObject({
    code: "active_run",
    run_id: run.id,
  });
  expect((await act(run.id, "resume")).status()).toBe(409);

  const continued = await act(run.id, "continue");
  expect(continued.status()).toBe(200);
  const raised = (await continued.json()) as Run;
  expect(raised.tokens.ceiling).toBe(3 * high);
  expect(raised.tokens.history).toHaveLength(1);
  expect(raised.tokens.history[0]).toMatchObject({
    spent: LARGE_TOKENS,
    from: 2 * high,
    to: 3 * high,
  });

  const done = await until(
    run.id,
    "the continued run ends",
    (now) => !ACTIVE.includes(now.status),
  );
  expect(done.status).toBe("done");
  expect(
    done.steps.map((step) => [step.status, step.attempt, step.tokens]),
  ).toEqual([
    ["done", 1, LARGE_TOKENS],
    ["done", 1, DEFAULT_TOKENS],
  ]);
  expect(done.tokens.spent).toBe(LARGE_TOKENS + DEFAULT_TOKENS);
  expect(done.tokens.history).toHaveLength(1);
  expect(done.counts).toMatchObject({ done: 2, failed: 0, items: 1 });
  // A second Continue has nothing to continue.
  expect((await act(run.id, "continue")).status()).toBe(409);
});

test("a run that reads a document again is cancelled while its step runs", async () => {
  const documents = [ids[SECOND]];
  expect((await estimateOf(documents)).reused_steps).toBe(1);
  const quote = await estimateOf(documents, documents);
  expect(quote.reused_steps).toBe(0);

  const run = await started(quote, documents, documents);
  const running = await until(
    run.id,
    "the step runs",
    (now) => now.steps[0].status !== "planned",
  );
  expect(running.steps[0].status).toBe("running");
  expect(running.current_agent_run_id).toBe(running.steps[0].agent_run_id);

  const cancel = await act(run.id, "cancel");
  expect(cancel.status()).toBe(200);
  const cancelled = await until(
    run.id,
    "the cancelled run ends",
    (now) => !ACTIVE.includes(now.status),
  );
  expect(cancelled.status).toBe("cancelled");
  expect(cancelled.steps[0].status).toBe("cancelled");
  expect(cancelled.finished_at).not.toBeNull();
  expect(cancelled.counts).toMatchObject({ done: 0, cancelled: 1 });
  expect((await act(run.id, "cancel")).status()).toBe(409);

  const list = (await (await context.request.get(RUNS)).json()) as RunList;
  expect(list.active_run_id).toBeNull();
  expect(list.runs.map((one) => [one.id, one.status])).toEqual(
    [...runs]
      .reverse()
      .map((id, index) => [id, index === 0 ? "cancelled" : "done"]),
  );
});

test("another user sees none of these runs and can act on none", async ({
  browser,
}) => {
  const other = await newContext(browser);
  try {
    const otherPage = await other.newPage();
    await otherPage.goto(cliLink(OTHER_EMAIL));
    await setPassword(otherPage, PASSWORD, "Sam Example");
    const list = await other.request.get(RUNS);
    expect(list.status()).toBe(200);
    expect(await list.json()).toEqual({ runs: [], active_run_id: null });
    expect(runs).toHaveLength(4);
    for (const id of runs) {
      expect((await other.request.get(`${RUNS}/${id}`)).status()).toBe(404);
      for (const action of ["cancel", "resume", "continue"])
        expect((await act(id, action, other)).status()).toBe(404);
    }
  } finally {
    await other.close();
  }
  // Nothing moved for the owner.
  expect((await read(runs[3])).status).toBe("cancelled");
});

test("the journey left no error in the browser console", () => {
  expect(errors).toEqual([]);
});
