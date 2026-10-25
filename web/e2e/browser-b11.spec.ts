import { execFileSync } from "node:child_process";

import { expect, test, type BrowserContext, type Page } from "@playwright/test";

import { AgentRun, RUN } from "./browser-b12-helpers";
import { PAGES, appLogs, endLease, windowTitles } from "./browser-b7-helpers";
import { currentLease } from "./browser-helpers";
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
 * Browser B11 against the real stack through Caddy, its browser included (compose profile
 * `browser`), no mocks. Person A goes the whole way of the module:
 * 1. an assistant's run of A (browser-b12-helpers.ts, in the worker's container) holds a lease
 *    on B12a's fixture site, reads it with the tools (B5) and asks A to sign in (B12a); A watches
 *    the live view on Account (B4), and while A's lease waits, B tries each of A's identifiers:
 *    the lease id, a fresh ticket of A's live view (on the websocket, through Caddy), the hand
 *    off, the later sign in, the input, and Caddy's check from outside: 403 or 404 every time,
 *    and none of A's in B's answers;
 * 2. A signs in with the live view's keyboard, under a name that is a planted token, and clicks
 *    I'm signed in (B12b); the run goes on, signed in;
 * 3. A opens a sign in session at an address A typed, with another planted token in it (B7);
 * 4. the canary: no session cookie, ticket, lease token, typed value or page text of the path is
 *    in the logs of the api, the worker, Caddy and the browser, nor anywhere in Redis.
 * Run it with the group (web/e2e/browser-all.config.ts says how).
 */
test.describe.configure({ mode: "serial", timeout: 240_000 });
test.skip(!caddyUrl, "set E2E_CADDY_URL with the Compose stack running");

const run = Date.now().toString(36);
const A = `e2e-browser-b11a-${run}@example.org`;
const B = `e2e-browser-b11b-${run}@example.org`;
// Planted tokens: the name A types into the page (and the page then shows), and the address A
// types for a sign in. Random, so nothing else in a log can hold them by chance.
const NAME = `cnry${crypto.randomUUID().replaceAll("-", "").slice(0, 16)}`;
const TYPED = `cnry${crypto.randomUUID().replaceAll("-", "").slice(0, 16)}`;
const SITE = `${PAGES}/b12/login.html?job=42&step=apply`;
const BANNER = { name: `Sign in to ${new URL(PAGES).hostname}` };
const CSRF = { "X-Requested-With": "jobapp" };
const VIEWER = crypto.randomUUID();

// The run of browser-b12-helpers.ts, which also prints its lease's token for the canary.
const HOLD =
  "    tools = LeaseTools(store, client, str(user_id), got.lease.lease_id, got.token)\n";
if (!RUN.includes(HOLD)) throw new Error("the assistant's run changed");
const TOKEN_RUN = RUN.replace(
  HOLD,
  `${HOLD}    say(step="token", token=got.token, lease=got.lease.lease_id)\n`,
);

/** Every key of the stack's Redis with its value, as text. */
function redisText(): string {
  const project = process.env.E2E_COMPOSE_PROJECT;
  if (!project)
    throw new Error("E2E_COMPOSE_PROJECT names the journey's own stack");
  const lua = [
    "local out = {}",
    "for _, k in ipairs(redis.call('KEYS', '*')) do",
    "  local t = redis.call('TYPE', k)['ok']",
    "  local v = t",
    "  if t == 'string' then v = redis.call('GET', k)",
    "  elseif t == 'list' then v = table.concat(redis.call('LRANGE', k, 0, -1), ' ')",
    "  elseif t == 'zset' then v = table.concat(redis.call('ZRANGE', k, 0, -1), ' ')",
    "  elseif t == 'set' then v = table.concat(redis.call('SMEMBERS', k), ' ')",
    "  elseif t == 'hash' then v = table.concat(redis.call('HGETALL', k), ' ') end",
    "  table.insert(out, k .. '=' .. v)",
    "end",
    "return out",
  ].join("\n");
  return execFileSync(
    "docker",
    [
      "compose",
      "-p",
      project,
      "-f",
      "compose.yml",
      "--profile",
      "browser",
      "exec",
      "-T",
      "redis",
      "redis-cli",
      "--no-raw",
      "EVAL",
      lua,
      "0",
    ],
    { cwd: root, encoding: "utf8", maxBuffer: 64 * 1024 * 1024 },
  );
}

/** What a websocket to `url` from `page` came to: open, or refused. */
async function websocket(page: Page, url: string): Promise<string> {
  return page.evaluate(
    (address) =>
      new Promise<string>((done) => {
        const ws = new WebSocket(address);
        const timer = setTimeout(() => {
          done("timeout");
        }, 15_000);
        ws.onopen = () => {
          clearTimeout(timer);
          ws.close();
          done("open");
        };
        ws.onerror = () => {
          clearTimeout(timer);
          done("refused");
        };
      }),
    url,
  );
}

let contexts: BrowserContext[] = [];
let pageA: Page;
let pageB: Page;
let errors: string[] = [];
const tickets: string[] = [];

test.beforeAll(async ({ browser }) => {
  for (const [email, name] of [
    [A, "Alex Example"],
    [B, "Blake Example"],
  ] as const) {
    const context = await newContext(browser);
    contexts.push(context);
    const page = await context.newPage();
    errors = [...errors, ...watchErrors(page)];
    await page.goto(cliLink(email));
    await setPassword(page, `browser b11 journey ${run} passphrase`, name);
    if (email === A) pageA = page;
    else pageB = page;
  }
  // Every ticket A's live view is handed, for the canary.
  pageA.on("response", async (response) => {
    if (!response.url().endsWith("/api/browser/live/ticket")) return;
    const body = (await response.json().catch(() => ({}))) as {
      ticket?: string;
    };
    if (body.ticket) tickets.push(body.ticket);
  });
});

test.afterAll(async () => {
  endLease(A);
  for (const page of [pageA, pageB]) {
    await page.request.delete("/api/browser/sessions/current", {
      headers: CSRF,
    });
  }
  for (const context of contexts) await context.close();
  contexts = [];
  clearAttempts([A, B]);
});

test("A's whole path, B refused on each of A's identifiers, and no secret in a log or Redis", async () => {
  const agent = new AgentRun(A, SITE, TOKEN_RUN);
  let token: string;
  let leaseId: string;
  const answersOfB: string[] = [];
  try {
    const held = await agent.next();
    expect(held.step).toBe("token");
    token = String(held.token);
    leaseId = String(held.lease);
    expect(token.length).toBeGreaterThan(20);
    expect(await agent.next()).toMatchObject({
      step: "ready",
      signed_in: false,
    });

    // 1. A watches; the run asks A to sign in; the lease waits for A.
    await pageA.goto("/account");
    await expect(
      pageA.getByText(
        "You are watching your browser. Take over to use it yourself.",
      ),
    ).toBeVisible({ timeout: 45_000 });
    agent.ask();
    const banner = pageA.getByRole("region", BANNER);
    await expect(banner).toBeVisible({ timeout: 30_000 });
    await expect(
      banner.getByText(
        "You have the keyboard and the mouse: sign in, then click I'm signed in.",
      ),
    ).toBeVisible({ timeout: 45_000 });
    expect(currentLease().lease?.lease_id).toBe(leaseId);

    // B, now, on each of A's identifiers.
    const fresh = await pageA.request.post("/api/browser/live/ticket", {
      headers: CSRF,
      data: { viewer: VIEWER },
    });
    expect(fresh.status()).toBe(201);
    const ticket = ((await fresh.json()) as { ticket: string }).ticket;
    tickets.push(ticket);
    const ws = `${caddyUrl!.replace(/^http/, "ws")}/vnc/websockify?ticket=${ticket}`;
    expect(await websocket(pageB, ws)).toBe("refused");
    const tries: Array<[string, string, unknown, number[]]> = [
      ["get", "/api/browser", undefined, [200]],
      ["post", "/api/browser/live/ticket", { viewer: VIEWER }, [403]],
      ["post", "/api/browser/takeover", { viewer: VIEWER }, [403]],
      ["post", "/api/browser/giveback", { viewer: VIEWER }, [403]],
      ["post", "/api/browser/handoff/done", undefined, [404]],
      ["post", "/api/browser/handoff/cancel", undefined, [404]],
      [
        "post",
        "/api/browser/handoff/later",
        { module: "application", ref: leaseId },
        [404],
      ],
      [
        "post",
        "/api/browser/handoff/later/done",
        { module: "application", ref: leaseId },
        [404],
      ],
      ["delete", "/api/browser/sessions/current", undefined, [404]],
      ["delete", `/api/browser/sessions/${leaseId}`, undefined, [404, 405]],
      ["get", `/api/browser/live/auth?ticket=${ticket}`, undefined, [404]],
    ];
    for (const [method, path, data, codes] of tries) {
      const answer = await pageB.request.fetch(path, {
        method,
        headers: CSRF,
        data,
      });
      expect(codes, `${method} ${path}`).toContain(answer.status());
      answersOfB.push(
        `${answer.status()} ${JSON.stringify(answer.headers())} ${await answer.text()}`,
      );
    }
    const theirs = (await (await pageB.request.get("/api/browser")).json()) as {
      lease: unknown;
      holder: string;
    };
    expect(theirs).toMatchObject({ lease: null, holder: "other" });
    await expect(banner).toBeVisible();
    expect(currentLease().lease).toMatchObject({
      lease_id: leaseId,
      state: "waiting_person",
    });

    // 2. A signs in with the live view's keyboard, and says so.
    await pageA.getByRole("button", { name: "Keyboard" }).click();
    const keys = pageA.getByLabel("Type into your browser");
    await keys.press("Tab");
    await keys.pressSequentially(NAME);
    await keys.press("Enter");
    await expect
      .poll(windowTitles, { timeout: 30_000 })
      .toContain(`Example Jobs: signed in as ${NAME}`);
    await banner.getByRole("button", { name: "I'm signed in" }).click();
    expect(await agent.next()).toMatchObject({
      step: "answer",
      error: false,
      signed_in: true,
    });
    expect(await agent.next()).toMatchObject({ step: "lease", same: true });
  } finally {
    await agent.close();
  }
  await expect.poll(() => currentLease().lease, { timeout: 30_000 }).toBeNull();

  // 3. A sign in session at an address A typed.
  const started = await pageA.request.post("/api/browser/sessions", {
    headers: CSRF,
    data: {
      purpose: "signin",
      address: `${PAGES}/b12/login.html?job=${TYPED}&step=signin`,
    },
  });
  expect(started.status()).toBe(201);
  await expect
    .poll(windowTitles, { timeout: 30_000 })
    .toContain(`job ${TYPED}`);
  const ended = await pageA.request.delete("/api/browser/sessions/current", {
    headers: CSRF,
  });
  expect(ended.status()).toBe(204);
  await expect.poll(() => currentLease().lease, { timeout: 30_000 }).toBeNull();

  // 4. The canary.
  const cookies = (
    await Promise.all([pageA, pageB].map((page) => page.context().cookies()))
  )
    .flat()
    .map((cookie) => cookie.value)
    .filter((value) => value.length >= 20);
  expect(cookies.length).toBeGreaterThanOrEqual(2);
  expect(tickets.length).toBeGreaterThanOrEqual(2);
  const planted: Record<string, string> = {
    "the lease token": token,
    "the typed name and the page's text": NAME,
    "the typed address": TYPED,
  };
  cookies.forEach((value, n) => (planted[`cookie ${n}`] = value));
  tickets.forEach((value, n) => (planted[`ticket ${n}`] = value));
  const logs = appLogs();
  expect(logs).toContain("/vnc/websockify?[redacted]"); // Caddy saw the viewers, masked
  const redis = redisText();
  for (const [name, value] of Object.entries(planted)) {
    expect(logs.includes(value), `${name} in the logs`).toBe(false);
    expect(redis.includes(value), `${name} in Redis`).toBe(false);
    for (const answer of answersOfB)
      expect(answer.includes(value), `${name} in B's answers`).toBe(false);
  }
  // The lease id is no secret (the logs name it), but B is never told it.
  for (const answer of answersOfB) expect(answer).not.toContain(leaseId);
  expect(errors).toEqual([]);
});
