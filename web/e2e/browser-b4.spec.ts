import { expect, test, type BrowserContext, type Page } from "@playwright/test";

import { caddyLog, fieldValue } from "./browser-b4-helpers";
import { endLease, startAgentLease } from "./browser-b7-helpers";
import { probe } from "./browser-helpers";
import {
  caddyUrl,
  clearAttempts,
  cliLink,
  makeShots,
  newContext,
  setPassword,
  watchErrors,
} from "./stack";

/**
 * Browser B4 against the real stack through Caddy, the browser service included (compose profile
 * `browser`), no mocks: an assistant's lease of person A runs (started through the api's lease
 * store, as the worker does: since B7 a person's own session has no debugging port and nothing
 * to take over); A opens Account, sees the canvas draw the page the lease's Chromium shows, view
 * only, takes over, types into its field, and the value reads back from that Chromium; after
 * Give back nothing typed reaches it. Person B gets no ticket, and A's ticket opens nothing from
 * B's session. Caddy's log holds no ticket.
 *
 * The page is a `data:` document (no network, no real site) whose field covers the whole tab, so
 * a click anywhere on the page lands in it. Run it with the group
 * (web/e2e/browser-all.config.ts says how).
 */
test.describe.configure({ mode: "serial", timeout: 120_000 });
test.skip(!caddyUrl, "set E2E_CADDY_URL with the Compose stack running");

const run = Date.now().toString(36);
const A = `e2e-browser-a-${run}@example.org`;
const B = `e2e-browser-b-${run}@example.org`;
const CSRF = { "X-Requested-With": "jobapp" };
const PAGE =
  "data:text/html,<body style='margin:0;background:rgb(255,0,170)'>" +
  "<input id=q autofocus style='position:fixed;inset:0;width:100%;height:100%;" +
  "border:0;font-size:40px;background:rgb(255,0,170)'></body>";
const shots = makeShots("browser-b4", "live");

let contexts: BrowserContext[] = [];
let pageA: Page;
let pageB: Page;
const tickets: string[] = [];

/** Whether the live view's canvas shows the page's own colour somewhere. */
async function drawsThePage(page: Page): Promise<boolean> {
  return page.getByTestId("live-screen").evaluate((screen) => {
    const canvas = screen.querySelector("canvas");
    const context = canvas?.getContext("2d");
    if (!canvas || !context || canvas.width === 0) return false;
    const { data } = context.getImageData(0, 0, canvas.width, canvas.height);
    for (let i = 0; i < data.length; i += 4 * 97) {
      if (data[i] === 255 && data[i + 1] === 0 && data[i + 2] === 170)
        return true;
    }
    return false;
  });
}

test.beforeAll(async ({ browser }) => {
  for (const [email, name] of [
    [A, "Alex Example"],
    [B, "Blake Example"],
  ] as const) {
    const context = await newContext(browser);
    contexts.push(context);
    const page = await context.newPage();
    watchErrors(page);
    await page.goto(cliLink(email));
    await setPassword(page, `browser b4 journey ${run} passphrase`, name);
    if (email === A) pageA = page;
    else pageB = page;
  }
  // Every ticket the pages ask for, to look for it in Caddy's log at the end.
  pageA.on("response", (response) => {
    if (!response.url().endsWith("/api/browser/live/ticket")) return;
    void response
      .json()
      .then((body: { ticket?: string }) => {
        if (body.ticket) tickets.push(body.ticket);
      })
      .catch(() => undefined);
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

test("A watches the browser draw the page, takes over and types", async () => {
  startAgentLease(A);
  await pageA.goto("/account");
  await expect(
    pageA.getByText(
      "You are watching your browser. Take over to use it yourself.",
    ),
  ).toBeVisible({ timeout: 45_000 });
  probe("navigate", PAGE, "20");
  await expect.poll(() => drawsThePage(pageA), { timeout: 20_000 }).toBe(true);
  await shots(pageA, "watching");

  // View only: a key pressed on the screen reaches nothing.
  await pageA.getByTestId("live-screen").click();
  await pageA.keyboard.type("no");
  expect(fieldValue("#q")).toBe("");

  await pageA.getByRole("button", { name: "Take over" }).click();
  await expect(pageA.getByRole("button", { name: "Give back" })).toBeVisible();
  await pageA.getByTestId("live-screen").click();
  await pageA.keyboard.type("hello b4");
  await expect
    .poll(() => fieldValue("#q"), { timeout: 10_000 })
    .toBe("hello b4");
  await shots(pageA, "typing");

  await pageA.getByRole("button", { name: "Give back" }).click();
  await expect(pageA.getByRole("button", { name: "Take over" })).toBeVisible();
  await pageA.getByTestId("live-screen").click();
  await pageA.keyboard.type("zz");
  expect(fieldValue("#q")).toBe("hello b4");
});

test("B never sees A's browser", async () => {
  const forB = await pageB.request.post("/api/browser/live/ticket", {
    headers: CSRF,
    data: { viewer: crypto.randomUUID() },
  });
  expect(forB.status()).toBe(403);

  const ofA = await pageA.request.post("/api/browser/live/ticket", {
    headers: CSRF,
    data: { viewer: crypto.randomUUID() },
  });
  expect(ofA.status()).toBe(201);
  const { url, ticket } = (await ofA.json()) as { url: string; ticket: string };
  tickets.push(ticket);
  await pageB.goto("/account");
  const opened = await pageB.evaluate(
    (address) =>
      new Promise<string>((resolve) => {
        const socket = new WebSocket(
          `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}${address}`,
        );
        socket.onopen = () => {
          socket.close();
          resolve("open");
        };
        socket.onerror = () => {
          resolve("refused");
        };
      }),
    url,
  );
  expect(opened).toBe("refused");
  // The check itself is Caddy's alone.
  const asked = await pageA.request.get("/api/browser/live/auth");
  expect(asked.status()).toBe(404);
});

test("Caddy's log holds no ticket", () => {
  expect(tickets.length).toBeGreaterThan(0);
  const log = caddyLog();
  expect(log).toContain("/vnc/websockify[redacted]");
  for (const ticket of tickets) expect(log).not.toContain(ticket);
});
