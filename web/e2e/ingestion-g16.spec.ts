import { expect, test, type BrowserContext, type Page } from "@playwright/test";

import {
  caddyUrl,
  clearAttempts,
  cliLink,
  newContext,
  setPassword,
  watchErrors,
} from "./stack";

/**
 * Task G16 of the ingestion module against the real stack through Caddy, no mocks, through the
 * api with the browser's session (the page that shows these figures is G11's): a new user
 * uploads two text fixtures, asks for the estimate of a run over them and reads the token
 * range and the ceiling computed by hand for those sizes, reads the consent state (none),
 * records the consent and reads it back. Every document is made up (a fictitious person).
 *
 * Locally, on a scratch Compose project (its own volumes, torn down afterwards):
 *   HTTP_PORT=8116 docker compose -p jobapp-g16 -f compose.yml -f compose.mac.yml up -d --build --wait
 *   docker compose -p jobapp-g16 -f compose.yml exec -T api alembic upgrade head
 *   E2E_CADDY_URL=http://127.0.0.1:8116 E2E_COMPOSE_PROJECT=jobapp-g16 \
 *     pnpm --dir web exec playwright test -c e2e/ingestion-all.config.ts ingestion-g16
 *   docker compose -p jobapp-g16 -f compose.yml down -v
 */
test.describe.configure({ mode: "serial" });
test.skip(!caddyUrl, "set E2E_CADDY_URL with the Compose stack running");

const run = Date.now().toString(36);
const EMAIL = `e2e-ingestion-g16-${run}@example.org`;
const NAME = "Robin Example";
const PASSWORD = `ingestion estimate ${run} passphrase`;

const DOCUMENTS = "/api/ingestion/documents";
const ESTIMATE = "/api/ingestion/runs/estimate";
const CONSENT = "/api/ingestion/consent";
const CSRF = { "X-Requested-With": "jobapp" };

/** A made up text of exactly `size` characters: lines of plain words, a letter at each end. */
function fixture(title: string, size: number): string {
  const line = `${title}: Robin Example led a made up project in Nantes for a made up client.\n`;
  const text = line.repeat(Math.ceil(size / line.length)).slice(0, size - 1);
  return `${text}x`;
}

// "Tokens" of the plan, by hand. A part of 30,000 characters: T = 10,000 and R = 1, so
// 21,000 + 10,000 * 6 / 2 = 51,000 input at the low end and 70,000 + 10,000 * 20 / 2 = 170,000
// at the high end. A part of 50,000 characters: T = 16,667 and R = 2, so
// 28,000 + 16,667 * 7 / 2 = 86,334.5 and 84,000 + 16,667 * 23 / 2 = 275,670.5. Each step writes
// 1,500 tokens at the low end and 8,000 at the high end.
// L = 51,000 + 86,334.5 + 3,000 = 140,334.5, rounded up once.
// H = 170,000 + 275,670.5 + 16,000 = 461,670.5, rounded up once. C = 2 * H.
const LOW = 140_335;
const HIGH = 461_671;
const CEILING = 923_342;

interface Estimate {
  documents: {
    id: string;
    name: string;
    characters: number;
    images: number;
    steps: number;
    reused_steps: number;
  }[];
  unreadable: unknown[];
  characters: number;
  steps: number;
  reused_steps: number;
  credential_kind: string | null;
  model: string | null;
  tokens: { low: number; high: number; ceiling: number };
  cost: unknown;
  consent: { version: number; accepted: boolean };
  can_start: boolean;
  blockers: { code: string; reason: string }[];
}

interface Consent {
  version: number;
  text: string;
  reminder: string;
  accepted: boolean;
  accepted_at: string | null;
}

let context: BrowserContext;
let page: Page;
let errors: string[];
const ids: string[] = [];

test.beforeAll(async ({ browser }) => {
  context = await newContext(browser);
  page = await context.newPage();
  errors = watchErrors(page, []);
});

test.afterAll(async () => {
  await context.close();
  clearAttempts([EMAIL]);
});

async function upload(name: string, text: string): Promise<string> {
  const stored = await context.request.post(DOCUMENTS, {
    headers: { ...CSRF, "X-Filename": `UTF-8''${name}` },
    data: Buffer.from(text, "utf8"),
  });
  expect(stored.status()).toBe(201);
  return ((await stored.json()) as { id: string }).id;
}

test("a new user signs in and adds two text documents", async () => {
  await page.goto(cliLink(EMAIL));
  await setPassword(page, PASSWORD, NAME);
  ids.push(await upload("dossier.md", fixture("Dossier", 30_000)));
  ids.push(await upload("cv.md", fixture("CV", 50_000)));
  expect(new Set(ids).size).toBe(2);
});

test("the estimate of a run over them is the range and the ceiling computed by hand", async () => {
  const response = await context.request.post(ESTIMATE, {
    headers: CSRF,
    data: { document_ids: ids },
  });
  expect(response.status()).toBe(200);
  expect(response.headers()["cache-control"]).toBe("no-store");
  const estimate = (await response.json()) as Estimate;
  expect(
    estimate.documents.map((one) => [one.name, one.characters, one.steps]),
  ).toEqual([
    ["dossier.md", 30_000, 1],
    ["cv.md", 50_000, 1],
  ]);
  expect(estimate.characters).toBe(80_000);
  expect(estimate.steps).toBe(2);
  expect(estimate.reused_steps).toBe(0);
  expect(estimate.unreadable).toEqual([]);
  expect(estimate.tokens).toEqual({ low: LOW, high: HIGH, ceiling: CEILING });
  // A user who has saved no credential yet still reads the estimate, and what is missing.
  expect(estimate.credential_kind).toBeNull();
  expect(estimate.model).toBeNull();
  expect(estimate.cost).toBeNull();
  expect(estimate.can_start).toBe(false);
  expect(estimate.blockers.map((one) => one.code)).toEqual(["no_credential"]);
  expect(estimate.consent).toEqual({ version: 2, accepted: false });
});

test("a selection the api cannot take is refused in words, and nobody reads it signed out", async ({
  browser,
}) => {
  const none = await context.request.post(ESTIMATE, {
    headers: CSRF,
    data: { document_ids: [] },
  });
  expect(none.status()).toBe(422);
  expect(((await none.json()) as { detail: string }).detail).toBe(
    "Select at least one document.",
  );
  const unknown = await context.request.post(ESTIMATE, {
    headers: CSRF,
    data: { document_ids: [ids[0], "00000000-0000-4000-8000-000000000000"] },
  });
  expect(unknown.status()).toBe(404);
  const anonymous = await browser.newContext({ baseURL: caddyUrl });
  try {
    const refused = await anonymous.request.post(ESTIMATE, {
      headers: CSRF,
      data: { document_ids: ids },
    });
    expect(refused.status()).toBe(401);
    expect((await anonymous.request.get(CONSENT)).status()).toBe(401);
  } finally {
    await anonymous.close();
  }
});

test("the consent is not given at first, then recorded, then read back", async () => {
  const before = await context.request.get(CONSENT);
  expect(before.status()).toBe(200);
  expect(before.headers()["cache-control"]).toBe("no-store");
  const none = (await before.json()) as Consent;
  // Version 2 (#115): version 1 and the sentence on the stored proposals.
  expect(none.version).toBe(2);
  expect(none.accepted).toBe(false);
  expect(none.accepted_at).toBeNull();
  expect(none.text).toContain("Anthropic");
  expect(none.text).toContain(
    "does not delete what Anthropic has already received",
  );
  expect(none.text).toContain(
    "The proposals it writes are stored in your account until you accept or reject them.",
  );

  // A version the app does not show is refused, and nothing is recorded.
  const other = await context.request.post(CONSENT, {
    headers: CSRF,
    data: { version: none.version + 1 },
  });
  expect(other.status()).toBe(422);
  expect(
    ((await (await context.request.get(CONSENT)).json()) as Consent).accepted,
  ).toBe(false);

  const given = await context.request.post(CONSENT, {
    headers: CSRF,
    data: { version: none.version },
  });
  expect(given.status()).toBe(200);
  const recorded = (await given.json()) as Consent;
  expect(recorded.accepted).toBe(true);
  expect(recorded.accepted_at).not.toBeNull();

  const after = (await (await context.request.get(CONSENT)).json()) as Consent;
  expect(after.accepted).toBe(true);
  expect(after.accepted_at).toBe(recorded.accepted_at);

  // Given a second time: the first date stays, and the estimate says the consent is there.
  const again = await context.request.post(CONSENT, {
    headers: CSRF,
    data: { version: none.version },
  });
  expect(((await again.json()) as Consent).accepted_at).toBe(
    recorded.accepted_at,
  );
  const estimate = await context.request.post(ESTIMATE, {
    headers: CSRF,
    data: { document_ids: ids },
  });
  expect(((await estimate.json()) as Estimate).consent).toEqual({
    version: 2,
    accepted: true,
  });
});

test("the journey left no error in the browser console", () => {
  expect(errors).toEqual([]);
});
