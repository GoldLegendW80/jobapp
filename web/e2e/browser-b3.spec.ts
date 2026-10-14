import { expect, test, type BrowserContext, type Page } from "@playwright/test";

import { currentLease } from "./browser-helpers";
import {
  caddyUrl,
  clearAttempts,
  cliLink,
  newContext,
  setPassword,
  watchErrors,
} from "./stack";

/**
 * Browser B3 against the real stack through Caddy, the browser service included (compose
 * profile `browser`), no mocks: person A signs in, asks the api for their browser from the
 * page's own session, starts a session and is the holder (`holder: you`), and browserd holds a
 * lease of A's profile. Person B, signed in beside, asks too and gets a place in the queue,
 * never A's lease. A ends the session; B asks again and holds it.
 *
 * Locally, on a scratch Compose project (its own volumes, torn down afterwards):
 *   BROWSER_CONTROL_SECRET=<hex> HTTP_PORT=8097 docker compose -p jobapp-b3 -f compose.yml \
 *     -f compose.mac.yml --profile browser up -d --wait
 *   docker compose -p jobapp-b3 -f compose.yml exec -T api alembic upgrade head
 *   E2E_CADDY_URL=http://127.0.0.1:8097 E2E_COMPOSE_PROJECT=jobapp-b3 \
 *     pnpm --dir web exec playwright test browser-b3.spec.ts
 *   docker compose -p jobapp-b3 -f compose.yml --profile browser down -v
 */
test.describe.configure({ mode: "serial", timeout: 120_000 });
test.skip(!caddyUrl, "set E2E_CADDY_URL with the Compose stack running");

const run = Date.now().toString(36);
const A = `e2e-browser-a-${run}@example.org`;
const B = `e2e-browser-b-${run}@example.org`;
const CSRF = { "X-Requested-With": "jobapp" };

type Browser = {
  available: boolean;
  profile: { exists: boolean };
  lease: { id: string; purpose: string; state: string } | null;
  holder: "you" | "other" | null;
  queue: { purpose: string; position: number; estimated_wait_s: number }[];
};

let contexts: BrowserContext[] = [];
let pageA: Page;
let pageB: Page;
const errors: string[][] = [];

type Session = {
  lease: { id: string; purpose: string } | null;
  queue: { purpose: string; position: number } | null;
};

async function browserOf(page: Page): Promise<Browser> {
  const answer = await page.request.get("/api/browser");
  expect(answer.status()).toBe(200);
  expect(answer.headers()["cache-control"]).toBe("no-store");
  return (await answer.json()) as Browser;
}

test.beforeAll(async ({ browser }) => {
  const people: [string, string][] = [
    [A, "Alex Example"],
    [B, "Blake Example"],
  ];
  for (const [email, name] of people) {
    const context = await newContext(browser);
    contexts.push(context);
    const page = await context.newPage();
    errors.push(watchErrors(page));
    await page.goto(cliLink(email));
    await setPassword(page, `browser b3 journey ${run} passphrase`, name);
    if (email === A) pageA = page;
    else pageB = page;
  }
});

test.afterAll(async () => {
  for (const page of [pageA, pageB]) {
    await page.request.delete("/api/browser/sessions/current", {
      headers: CSRF,
    });
  }
  for (const context of contexts) await context.close();
  contexts = [];
  clearAttempts([A, B]);
});

test("person A starts a session and holds the browser", async () => {
  const before = await browserOf(pageA);
  expect(before.available).toBe(true);
  expect(before.lease).toBeNull();

  const started = await pageA.request.post("/api/browser/sessions", {
    headers: CSRF,
    data: { purpose: "look" },
  });
  expect(started.status()).toBe(201);
  const lease = ((await started.json()) as Session).lease;
  expect(lease?.purpose).toBe("look");

  const after = await browserOf(pageA);
  expect(after.holder).toBe("you");
  expect(after.lease?.id).toBe(lease?.id);
  expect(after.profile.exists).toBe(true);
  // browserd holds that lease, on the person's profile, for a person at the keyboard.
  const held = currentLease().lease;
  expect(held?.lease_id).toBe(lease?.id);
  expect(held?.purpose).toBe("takeover");
});

test("person B gets a place in the queue and never A's lease", async () => {
  const leaseOfA = (await browserOf(pageA)).lease?.id ?? "";
  expect(leaseOfA).not.toBe("");
  const asked = await pageB.request.post("/api/browser/sessions", {
    headers: CSRF,
    data: { purpose: "look" },
  });
  expect(asked.status()).toBe(202);
  expect(((await asked.json()) as Session).queue?.position).toBe(1);
  const seen = await browserOf(pageB);
  expect(seen.holder).toBe("other");
  expect(seen.lease).toBeNull();
  expect(seen.queue).toEqual([
    expect.objectContaining({ purpose: "look", position: 1 }),
  ]);
  expect(JSON.stringify(seen)).not.toContain(leaseOfA);
  expect(await asked.text()).not.toContain(leaseOfA);
});

test("A ends the session and B takes the turn", async () => {
  const ended = await pageA.request.delete("/api/browser/sessions/current", {
    headers: CSRF,
  });
  expect(ended.status()).toBe(204);
  expect((await browserOf(pageA)).holder).toBeNull();
  const took = await pageB.request.post("/api/browser/sessions", {
    headers: CSRF,
    data: { purpose: "look" },
  });
  expect(took.status()).toBe(201);
  expect((await browserOf(pageB)).holder).toBe("you");
  expect((await browserOf(pageA)).holder).toBe("other");
  for (const seen of errors) expect(seen).toEqual([]);
});
