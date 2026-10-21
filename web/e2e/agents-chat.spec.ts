import { randomBytes } from "node:crypto";

import { expect, test, type BrowserContext, type Page } from "@playwright/test";

import {
  caddyUrl,
  clearAttempts,
  cliLink,
  makeShots,
  newContext,
  setPassword,
  watchErrors,
} from "./stack";

const shots = makeShots("agents-a4", "chat");

/**
 * Chat about a job (agents A4) against the real stack through Caddy, the worker replaying the
 * recorded chat stream (AGENTS_FAKE_BACKEND, no model call). The recorded answer repeats the
 * message and says how many earlier messages its prompt held, which is how this journey sees
 * that each message's prompt is rebuilt from the database: open the chat on a job, send, watch
 * the answer stream, reload and find the conversation (and the panel) again, send a second
 * message whose prompt holds the first exchange, delete the conversation.
 *
 *   HTTP_PORT=8128 docker compose -p jobapp-a4 -f compose.yml -f compose.mac.yml -f tests/compose.a2-e2e.yml \
 *     up -d --build --wait
 *   docker compose -p jobapp-a4 -f compose.yml exec -T api alembic upgrade head
 *   E2E_CADDY_URL=http://127.0.0.1:8128 E2E_COMPOSE_PROJECT=jobapp-a4 \
 *     pnpm --dir web exec playwright test agents-chat.spec.ts
 *   docker compose -p jobapp-a4 -f compose.yml -f tests/compose.a2-e2e.yml down -v --rmi local
 */
test.describe.configure({ mode: "serial" });
test.skip(!caddyUrl, "set E2E_CADDY_URL with the Compose stack running");
test.setTimeout(120_000);

const run = Date.now().toString(36);
const EMAIL = `e2e-chat-${run}@example.org`;
const PASSWORD = `Pw ${randomBytes(12).toString("base64url")}`;
const tail = () => randomBytes(30).toString("base64url").replace(/[-_]/g, "x");
// A made up key of the right shape: the fake backend never sends it anywhere.
const KEY = `sk-ant-api03-e2echat${tail()}${tail()}Ch4t`;
const CSRF = { "X-Requested-With": "jobapp" };
const FIRST = "Does this role fit my profile?";
const SECOND = "What should I stress in my letter?";

let context: BrowserContext;
let page: Page;
let errors: string[];
let jobUrl = "";

const panel = () => page.getByRole("dialog", { name: "Chat about this job" });
const composer = () => panel().getByRole("textbox", { name: "Message" });
const messages = () => panel().getByTestId("chat-message");
const live = () => panel().getByTestId("chat-live-reply");

test.beforeAll(async ({ browser }) => {
  context = await newContext(browser);
  page = await context.newPage();
  errors = watchErrors(page);
});

test.afterAll(async () => {
  await context.close();
  clearAttempts([EMAIL]);
});

test("the Chat button opens an empty conversation with example questions", async () => {
  const link = cliLink(EMAIL);
  await page.goto(link);
  await setPassword(
    page,
    PASSWORD,
    link.startsWith("/invite/") ? "E2E Chat" : undefined,
  );
  const created = await page.request.post("/api/jobs", {
    headers: CSRF,
    data: {
      company: "Example Agents",
      title: "Applied AI Engineer",
      location: "Paris, France",
      posting_text:
        "We build LLM agents with tool calling and evaluation.\n\nFive years of Python.",
    },
  });
  expect(created.status()).toBe(201);
  const job = (await created.json()) as { id: string };
  jobUrl = `/jobs/${job.id}`;
  const saved = await page.request.put("/api/me/model-credential", {
    headers: CSRF,
    data: { kind: "anthropic_api_key", secret: KEY },
  });
  expect(saved.status()).toBe(200);

  await page.goto(jobUrl);
  await page.getByRole("button", { name: "Chat", exact: true }).click();
  await expect(panel()).toBeVisible();
  await expect(page).toHaveURL(`${jobUrl}?chat=open`);
  await expect(panel().getByTestId("chat-empty")).toContainText(
    "Ask anything about this posting.",
  );
  await expect(
    panel()
      .getByRole("list", { name: "Example questions" })
      .getByRole("button"),
  ).toHaveCount(3);
  await expect(composer()).toBeEnabled();
  await expect(page.locator("body")).not.toContainText(/[–—]| - /);
  await shots(page, "empty", false);
});

test("a message streams its answer live, then the stored answer replaces it", async () => {
  // Keep the thread busy until the pending-message controls have been exercised, even if
  // the fake worker finishes before the browser reaches them.
  let release = () => {};
  const inspected = new Promise<void>((resolve) => {
    release = resolve;
  });
  const threadRead = /\/api\/agents\/threads\/[^/?]+$/;
  await page.route(threadRead, async (route) => {
    const response = await route.fetch();
    const detail = (await response.json()) as {
      active_run_id: string | null;
      messages: Array<{ role: string }>;
    };
    if (
      detail.active_run_id === null &&
      detail.messages.some((message) => message.role === "assistant")
    ) {
      await inspected;
    }
    await route.fulfill({ response });
  });
  try {
    await panel().getByRole("button", { name: FIRST }).click();
    await expect(composer()).toHaveValue(FIRST);
    await composer().press("Enter");
    await expect(messages().first()).toContainText(FIRST);
    await expect(live()).toBeVisible();
    // The whole conversation is on screen: the message and the answer being written.
    await expect(messages()).toHaveCount(1);
    await expect(
      panel().getByText("Older messages are not shown."),
    ).toHaveCount(0);
    await expect(panel().getByRole("button", { name: "Send" })).toBeDisabled();
    // The recorded stream: the agent reads the posting, says so, then answers.
    await expect(live()).toContainText(
      /Reading the posting|Let me read the posting again\./,
      { timeout: 15_000 },
    );
    await composer().fill("Keep this as a draft");
    await composer().press("Enter");
    const held = panel().getByTestId("chat-held-message");
    await expect(held).toContainText("Keep this as a draft");
    await expect(held).toContainText("Pending");
    await expect(
      panel().getByText(/Your message will be sent when/),
    ).toHaveAttribute("aria-live", "polite");
    for (const width of [320, 768, 1024, 1440]) {
      await page.setViewportSize({ width, height: 900 });
      await expect(held.getByRole("button", { name: "Cancel" })).toBeVisible();
      await shots(page, `held-${width}`, false);
    }
    await held.getByRole("button", { name: "Cancel" }).click();
    await expect(held).toHaveCount(0);
    await expect(composer()).toHaveValue("Keep this as a draft");
    await expect(composer()).toBeFocused();
    release();
    await shots(page, "streaming", false);
    await expect(live()).toHaveCount(0, { timeout: 30_000 });
    const answer = messages().nth(1);
    await expect(answer).toHaveAttribute("data-role", "assistant");
    await expect(answer).toContainText(`You asked: ${FIRST}`);
    await expect(answer).toContainText(
      "This conversation held 0 earlier messages when you wrote, 0 left out.",
    );
    await expect(panel().getByRole("status")).toContainText(
      `Answer: You asked: ${FIRST}`,
    );
    await expect(panel().getByRole("button", { name: "Send" })).toBeEnabled();
    await expect(composer()).toHaveValue("Keep this as a draft");
    await expect(messages()).toHaveCount(2);
    const listed = await page.request.get("/api/agents/runs?agent=job_chat");
    expect(((await listed.json()) as { items: unknown[] }).items).toHaveLength(
      1,
    );
  } finally {
    release();
    await page.unroute(threadRead);
  }
});

test("the conversation and the open panel survive a reload", async () => {
  await page.reload();
  await expect(panel()).toBeVisible();
  await expect(messages()).toHaveCount(2);
  await expect(messages().nth(0)).toContainText(FIRST);
  await expect(messages().nth(1)).toContainText(`You asked: ${FIRST}`);
  // Nothing announced again after the reload.
  await expect(panel().getByRole("status")).toBeEmpty();
  await shots(page, "history", false);
});

test("the second message's prompt is rebuilt with the first exchange", async () => {
  await composer().fill(`${SECOND}\nOne line more.`);
  await composer().press("Enter");
  await expect(messages()).toHaveCount(3);
  await expect(live()).toBeVisible();
  await expect(live()).toHaveCount(0, { timeout: 30_000 });
  await expect(messages()).toHaveCount(4);
  const answer = messages().nth(3);
  await expect(answer).toContainText(`You asked: ${SECOND}`);
  await expect(answer).toContainText(
    "This conversation held 2 earlier messages when you wrote, 0 left out.",
  );
  // The run log lists both chat runs.
  const listed = await page.request.get("/api/agents/runs?agent=job_chat");
  const runs = (await listed.json()) as { items: Array<{ status: string }> };
  expect(runs.items.map((item) => item.status)).toEqual(["done", "done"]);
  await shots(page, "second-answer", false);
});

test("deleting the conversation empties it, also after a reload", async () => {
  await panel().getByRole("button", { name: "Delete conversation" }).click();
  await expect(panel().getByText("Delete this conversation?")).toBeVisible();
  await shots(page, "delete-confirm", false);
  await panel().getByRole("button", { name: "Delete", exact: true }).click();
  await expect(page.getByText("Conversation deleted.")).toBeVisible();
  await expect(panel().getByTestId("chat-empty")).toBeVisible();
  await page.reload();
  await expect(panel().getByTestId("chat-empty")).toBeVisible();
  await expect(messages()).toHaveCount(0);
  await page.keyboard.press("Escape");
  await expect(panel()).toHaveCount(0);
  await expect(page).toHaveURL(jobUrl);
  expect(errors).toEqual([]);
});
