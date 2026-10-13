import { execFileSync } from "node:child_process";
import { randomBytes } from "node:crypto";

import {
  expect,
  test,
  type BrowserContext,
  type Locator,
  type Page,
} from "@playwright/test";

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

const shots = makeShots("agents-33-streaming", "streaming");

/**
 * The answer appears while it is written (issue #33), against the real stack through Caddy, the
 * worker replaying partial messages from recorded streams (AGENTS_FAKE_BACKEND, no model call):
 * `long_answer` writes one answer in pieces of six characters every 0.1 s, `job_chat` the chat's.
 * Every journey asserts intermediate states, not only the final one: the text on screen is
 * caught while it is a strict beginning of the answer, several times, growing.
 *
 * In the run panel: the answer grows, a second tab opened in the middle gets the text so far,
 * a network loss in the middle ends with the whole answer exactly once, and a cancel in the
 * middle stops it. In the chat: the answer grows in its bubble without being announced, the
 * stored answer takes its place without a gap, the list stays where a reader scrolled it, and
 * the spinner stands still under prefers-reduced-motion.
 *
 *   HTTP_PORT=8133 docker compose -p jobapp-a33 -f compose.yml -f tests/compose.a2-e2e.yml \
 *     up -d --build --wait
 *   docker compose -p jobapp-a33 -f compose.yml exec -T api alembic upgrade head
 *   E2E_CADDY_URL=http://127.0.0.1:8133 E2E_COMPOSE_PROJECT=jobapp-a33 \
 *     pnpm --dir web exec playwright test agents-streaming.spec.ts
 *   docker compose -p jobapp-a33 -f compose.yml -f tests/compose.a2-e2e.yml down -v --rmi local
 */
test.describe.configure({ mode: "serial" });
test.skip(!caddyUrl, "set E2E_CADDY_URL with the Compose stack running");
test.setTimeout(120_000);

const run = Date.now().toString(36);
const EMAIL = `e2e-live-${run}@example.org`;
const PASSWORD = `Pw ${randomBytes(12).toString("base64url")}`;
const tail = () => randomBytes(30).toString("base64url").replace(/[-_]/g, "x");
// A made up key of the right shape: the fake backend never sends it anywhere.
const KEY = `sk-ant-api03-e2elive${tail()}${tail()}L1ve`;
const CSRF = { "X-Requested-With": "jobapp" };
const QUESTION = "is a < b && c > d a fit?";
// What `long_answer` writes (api/app/agents/fake_streams/long_answer.json), for this message.
const LONG = [
  `You asked: ${QUESTION}`,
  "Here is a long answer, written slowly so that it can be watched while it grows.",
  "",
  "- First, the posting asks for Python and for agents that call tools: both are in the profile.",
  "- Second, it asks for evaluation work: comparisons such as a < b && b < c are fine in plain text.",
  "- Third, a few characters that take more than one byte: café, déjà vu, 日本語, 😀 and 👩‍💻.",
  "",
  "One gap stays: the posting wants five years and the profile shows fewer. I would rather say so than round up.",
  "",
  "What to check before applying: the location, the contract, and whether the team works in English. That is the whole answer, every character of it exactly once.",
].join("\n");

let context: BrowserContext;
let page: Page;
let errors: string[];
let jobUrl = "";

const panel = () => page.getByTestId("run-panel");
const steps = (on: Page = page) =>
  on.getByTestId("run-panel").getByRole("list", { name: "Steps" });
const writing = (on: Page = page) => steps(on).locator("li[data-live=true]");
const answers = (on: Page = page) => steps(on).locator("li[data-kind=text]");
const badge = () => panel().locator("[data-slot=badge]");

const chat = () => page.getByRole("dialog", { name: "Chat about this job" });
const composer = () => chat().getByRole("textbox", { name: "Message" });
const messages = () => chat().getByTestId("chat-message");
const liveReply = () => chat().getByTestId("chat-live-reply");
const liveText = () => chat().getByTestId("chat-live-text");

/**
 * Type a message and send it once the chat can send. The composer is enabled before that, and
 * Enter then holds the message, which is sent by itself when the conversation has loaded or
 * the previous answer is settled (issue #37). Waiting for the Send button keeps this helper
 * on the plain path: the message goes out at the moment Enter is pressed.
 */
async function ask(text: string) {
  await composer().fill(text);
  await expect(chat().getByRole("button", { name: "Send" })).toBeEnabled();
  await composer().press("Enter");
}

// A second question long enough for its answer, which repeats it, to be written for more than
// ten seconds (job_chat: four characters every 0.15 s): time to scroll up, watch the answer
// grow, scroll down and watch it grow again, each a second or so, whatever the runner's speed.
const SECOND = [
  "Second question, to scroll away from.",
  "It is a long one on purpose: the answer repeats it, so the answer is written for many seconds",
  "and there is time to scroll up, watch it grow, scroll down and watch it grow again.",
  "Nothing in it matters beyond its length, which is a few hundred characters.",
].join(" ");

async function startRun(script: string, message: string): Promise<string> {
  const started = await page.request.post("/api/agents/runs", {
    headers: CSRF,
    data: { agent: "echo", input: { message, script } },
  });
  expect(started.status()).toBe(202);
  return ((await started.json()) as { id: string }).id;
}

/** The text of the answer in the timeline (the item's body, without its "Answer" title). */
function body(item: Locator): Locator {
  return item.locator("p").last();
}

/**
 * Record every text `selector` shows from now on, in the page: an observer on the document sees
 * each change, so no intermediate state is missed between two polls of the test.
 */
async function watch(on: Page, selector: string, name: string) {
  await on.evaluate(
    ([target, key]) => {
      const seen: string[] = [];
      (window as unknown as Record<string, string[]>)[key] = seen;
      const note = () => {
        const node = document.querySelector(target);
        const text = node?.textContent ?? null;
        if (text !== null && seen.at(-1) !== text) seen.push(text);
      };
      note();
      new MutationObserver(note).observe(document.body, {
        subtree: true,
        childList: true,
        characterData: true,
      });
    },
    [selector, name] as const,
  );
  return () =>
    on.evaluate(
      (key) => (window as unknown as Record<string, string[]>)[key] ?? [],
      name,
    );
}

/** Many states, each a strict beginning of `whole`, never shrinking. */
function expectProgressive(seen: string[], whole: string, atLeast: number) {
  const partial = seen.filter((text) => text !== whole && text.length > 0);
  for (const text of partial) {
    expect(whole.startsWith(text), `not a beginning: ${text}`).toBe(true);
  }
  const lengths = partial.map((text) => text.length);
  expect(lengths).toEqual([...lengths].sort((a, b) => a - b));
  expect(new Set(lengths).size).toBeGreaterThanOrEqual(atLeast);
}

test.beforeAll(async ({ browser }) => {
  context = await newContext(browser);
  page = await context.newPage();
  errors = watchErrors(page);
});

test.afterAll(async () => {
  await context.close();
  clearAttempts([EMAIL]);
});

test("the answer grows in the run panel, then the stored step takes its place", async () => {
  const link = cliLink(EMAIL);
  await page.goto(link);
  await setPassword(
    page,
    PASSWORD,
    link.startsWith("/invite/") ? "E2E Live" : undefined,
  );
  const saved = await page.request.put("/api/me/model-credential", {
    headers: CSRF,
    data: { kind: "anthropic_api_key", secret: KEY },
  });
  expect(saved.status()).toBe(200);

  const id = await startRun("long_answer", QUESTION);
  await page.goto(`/runs/${id}`);
  await expect(panel()).toBeVisible();
  const seen = await watch(
    page,
    "[data-testid=run-panel] li[data-kind=text] p:last-of-type",
    "__answer",
  );
  // Visibly partial: the answer is being written and is not complete.
  await expect(writing()).toBeVisible({ timeout: 20_000 });
  await expect(writing()).toHaveAttribute("aria-busy", "true");
  await expect
    .poll(async () => (await body(writing()).textContent())?.length ?? 0)
    .toBeGreaterThan(60);
  const early = (await body(writing()).textContent()) ?? "";
  expect(early.length).toBeLessThan(LONG.length);
  expect(LONG.startsWith(early)).toBe(true);
  await expect(badge()).toHaveText("Running");
  await shots(page, "run-writing", false);
  // Still being written after four screenshots, and further on (the next piece is at most a
  // few hundred milliseconds away: wait for it rather than assume the screenshots took longer).
  await expect
    .poll(async () => (await body(writing()).textContent())?.length ?? 0)
    .toBeGreaterThan(early.length);
  const later = (await body(writing()).textContent()) ?? "";
  expect(later.length).toBeLessThan(LONG.length);
  expect(LONG.startsWith(later)).toBe(true);
  // Screen readers heard the title once, not the text.
  const region = panel().locator("p[role=status][aria-live=polite]");
  await expect(region).toHaveText("Answer");

  await expect(badge()).toHaveText("Done", { timeout: 40_000 });
  await expect(writing()).toHaveCount(0);
  await expect(answers()).toHaveCount(1);
  expect(await body(answers()).textContent()).toBe(LONG);
  expectProgressive(await seen(), LONG, 8);
  // The thinking block of the recorded stream never reached the page.
  await expect(page.locator("body")).not.toContainText("PRIVATE-REASONING");
  await expect(page.locator("body")).not.toContainText("&lt;");
  await shots(page, "run-done");
});

test("a second tab opened in the middle gets the text so far and the same answer", async () => {
  const id = await startRun("long_answer", QUESTION);
  await page.goto(`/runs/${id}`);
  await expect
    .poll(async () => (await body(writing()).textContent())?.length ?? 0, {
      timeout: 20_000,
    })
    .toBeGreaterThan(150);
  const second = await context.newPage();
  await second.goto(`/runs/${id}`);
  await expect(writing(second)).toBeVisible();
  const first = (await body(writing(second)).textContent()) ?? "";
  // Its first sight of the answer is everything written so far, not the first few characters.
  expect(first.length).toBeGreaterThan(150);
  expect(LONG.startsWith(first)).toBe(true);
  for (const tab of [page, second]) {
    await expect(answers(tab)).toHaveCount(1, { timeout: 40_000 });
    await expect(writing(tab)).toHaveCount(0, { timeout: 40_000 });
    expect(await body(answers(tab)).textContent()).toBe(LONG);
  }
  await second.close();
});

test("a network loss in the middle of the answer ends with the whole text once", async () => {
  const id = await startRun("long_answer", QUESTION);
  await page.goto(`/runs/${id}`);
  const seen = await watch(
    page,
    "[data-testid=run-panel] li[data-kind=text] p:last-of-type",
    "__cut",
  );
  await expect
    .poll(async () => (await body(writing()).textContent())?.length ?? 0, {
      timeout: 20_000,
    })
    .toBeGreaterThan(80);
  // Offline keeps the browser from reconnecting; restarting caddy cuts the open stream.
  await context.setOffline(true);
  execFileSync(
    "docker",
    [
      "compose",
      "-p",
      process.env.E2E_COMPOSE_PROJECT ?? "jobapp",
      "-f",
      "compose.yml",
      "restart",
      "caddy",
    ],
    { cwd: root, stdio: "ignore" },
  );
  await expect(panel().getByText("Reconnecting")).toBeVisible({
    timeout: 15_000,
  });
  // What was written before the cut stays on screen while the connection is away.
  const during = (await body(answers()).textContent()) ?? "";
  expect(during.length).toBeGreaterThan(80);
  expect(LONG.startsWith(during)).toBe(true);
  await context.setOffline(false);
  await expect(badge()).toHaveText("Done", { timeout: 60_000 });
  await expect(writing()).toHaveCount(0);
  // Exactly the full text, exactly once: no fragment twice, none missing.
  await expect(answers()).toHaveCount(1);
  expect(await body(answers()).textContent()).toBe(LONG);
  for (const text of await seen()) {
    expect(LONG.startsWith(text), `not a beginning: ${text}`).toBe(true);
  }
  const detail = await page.request.get(`/api/agents/runs/${id}`);
  const stored = (await detail.json()) as {
    output: { text: string };
    events: Array<{ kind: string }>;
  };
  expect(stored.output.text).toBe(LONG);
  // No delta was stored: one text event, whatever the number of pieces shown.
  expect(stored.events.map((event) => event.kind)).toEqual([
    "status",
    "status",
    "init",
    "thinking",
    "text",
    "usage",
    "status",
  ]);
});

test("a cancel in the middle of the answer stops it", async () => {
  const id = await startRun("long_answer", QUESTION);
  await page.goto(`/runs/${id}`);
  await expect
    .poll(async () => (await body(writing()).textContent())?.length ?? 0, {
      timeout: 20_000,
    })
    .toBeGreaterThan(60);
  await panel().getByRole("button", { name: "Cancel" }).click();
  await expect(badge()).toHaveText("Cancelled", { timeout: 15_000 });
  // Nothing is being written any more. The log keeps the cut text as a step (Claude Code
  // reports it), and the run has no output.
  await expect(writing()).toHaveCount(0);
  await expect(answers()).toHaveCount(1);
  const cut = (await body(answers()).textContent()) ?? "";
  expect(cut.length).toBeGreaterThan(60);
  expect(cut.length).toBeLessThan(LONG.length);
  expect(LONG.startsWith(cut)).toBe(true);
  const detail = await page.request.get(`/api/agents/runs/${id}`);
  expect(((await detail.json()) as { output: unknown }).output).toBeNull();
  await shots(page, "run-cancelled");
});

test("the chat answer grows in its bubble and the stored answer takes its place", async () => {
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
  jobUrl = `/jobs/${((await created.json()) as { id: string }).id}`;
  await page.goto(`${jobUrl}?chat=open`);
  await expect(chat()).toBeVisible();
  await expect(composer()).toBeEnabled();
  const seen = await watch(page, "[data-testid=chat-live-text]", "__chat");
  await ask(QUESTION);
  await expect(messages().first()).toContainText(QUESTION);
  await expect(liveReply()).toBeVisible();
  const final = `You asked: ${QUESTION}\nI read the posting again. This conversation held 0 earlier messages when you wrote, 0 left out.`;
  // The answer itself, while it is written: a strict beginning of it, then a longer one.
  await expect(liveReply()).toHaveAttribute("data-writing", "true", {
    timeout: 20_000,
  });
  await expect(liveText()).toContainText("You asked: is a <", {
    timeout: 20_000,
  });
  const early = (await liveText().textContent()) ?? "";
  expect(early.length).toBeLessThan(final.length);
  expect(final.startsWith(early)).toBe(true);
  await expect(liveReply()).toContainText("Writing");
  // Not announced while it grows.
  await expect(chat().getByRole("status")).toBeEmpty();
  await shots(page, "chat-writing", false);
  await expect(liveReply()).toHaveCount(0, { timeout: 30_000 });
  // From the last piece to the stored message the answer never left the screen: the count of
  // assistant bubbles goes from the live one to one stored, never through zero after the end.
  const answer = messages().nth(1);
  await expect(answer).toHaveAttribute("data-role", "assistant");
  await expect(answer).not.toHaveAttribute("data-settling", "true");
  expect(await answer.locator("div").textContent()).toBe(final);
  await expect(messages()).toHaveCount(2);
  await expect(chat().getByRole("status")).toContainText(
    `Answer: You asked: ${QUESTION}`,
  );
  // What was shown on the way: the first text ("Let me read the posting again."), then
  // beginnings of the answer, growing. The escaping of the prompt (PR #30) is in none of them.
  const states = await seen();
  for (const text of states) {
    expect(text).not.toContain("&lt;");
    expect(text).not.toContain("&amp;");
  }
  expectProgressive(
    states.filter((text) => text.startsWith("You asked")),
    final,
    5,
  );
  expect(
    states.some(
      (text) =>
        text !== "Let me read the posting again." &&
        "Let me read the posting again.".startsWith(text) &&
        text.length > 0,
    ),
  ).toBe(true);
  await shots(page, "chat-answered", false);
});

test("the list stays where the reader scrolled it while the answer grows", async () => {
  // A short window, so that two exchanges overflow the list.
  await page.setViewportSize({ width: 1280, height: 420 });
  const list = chat().getByTestId("chat-scroll");
  const metrics = () =>
    list.evaluate((node) => ({
      top: node.scrollTop,
      gap: node.scrollHeight - node.scrollTop - node.clientHeight,
      overflow: node.scrollHeight - node.clientHeight,
    }));
  // The answer to this question as far as it is written, 0 before it begins. The agent first
  // writes a sentence of thirty characters, calls a tool for three seconds, and only then
  // writes the answer: the text on screen is the answer once it begins with "You asked".
  // (Measured from that first sentence, a growth of 20 characters came about five seconds
  // later, the whole window of a poll: the wait passed or failed on a few milliseconds.)
  const written = async () => {
    const text = (await liveText().textContent()) ?? "";
    return text.startsWith("You asked") ? text.length : 0;
  };
  const whole = `You asked: ${SECOND}\nI read the posting again.`.length;
  await ask(SECOND);
  await expect(messages()).toHaveCount(3);
  // Sending brought the list to its bottom.
  await expect.poll(async () => (await metrics()).gap).toBeLessThan(2);
  expect((await metrics()).overflow).toBeGreaterThan(60);
  // The reader goes back to the top while the agent works.
  await list.evaluate((node) => {
    node.scrollTop = 0;
  });
  await expect(liveText()).toContainText("You asked: Second", {
    timeout: 20_000,
  });
  await expect(liveReply()).toHaveAttribute("data-writing", "true");
  const before = await written();
  const low = (await metrics()).overflow;
  // Most of the answer is still to come: the waits below need a few lines of it.
  expect(before).toBeLessThan(whole / 2);
  await expect.poll(written).toBeGreaterThan(before + 20);
  // The list itself got taller (a line was added), and the view stayed where it was.
  await expect
    .poll(async () => (await metrics()).overflow)
    .toBeGreaterThan(low);
  expect((await metrics()).top).toBe(0); // the growing text did not pull the view down
  // Back at the bottom: the list follows the text again.
  await list.evaluate((node) => {
    node.scrollTop = node.scrollHeight;
  });
  const mid = await written();
  const high = (await metrics()).overflow;
  await expect.poll(written).toBeGreaterThan(mid + 12);
  await expect
    .poll(async () => (await metrics()).overflow)
    .toBeGreaterThan(high);
  expect((await metrics()).gap).toBeLessThan(2);
  await expect(liveReply()).toHaveCount(0, { timeout: 30_000 });
  await expect(messages()).toHaveCount(4);
  await expect.poll(async () => (await metrics()).gap).toBeLessThan(2);
  await page.setViewportSize({ width: 1280, height: 800 });
});

test("the spinner stands still under prefers-reduced-motion", async () => {
  await page.emulateMedia({ reducedMotion: "reduce" });
  await ask("Third question.");
  await expect(liveReply()).toBeVisible();
  const spinner = liveReply().locator("svg").last();
  await expect
    .poll(() =>
      spinner.evaluate((node) => getComputedStyle(node).animationName),
    )
    .toBe("none");
  // The text still appears as it is written: motion is reduced, not the content.
  await expect(liveReply()).toHaveAttribute("data-writing", "true", {
    timeout: 20_000,
  });
  await page.emulateMedia({ reducedMotion: "no-preference" });
  await expect
    .poll(() =>
      spinner.evaluate((node) => getComputedStyle(node).animationName),
    )
    .not.toBe("none");
  await expect(liveReply()).toHaveCount(0, { timeout: 30_000 });
  await expect(messages()).toHaveCount(6);
  await page.keyboard.press("Escape");
  // The deliberate network loss leaves the browser's own lines about the cut stream.
  const cut =
    /net::ERR_[A-Z_]+ @ .*\/api\/agents\/runs\/[0-9a-f-]+(\/stream\?after=\d+)?$/;
  expect(errors.filter((line) => !cut.test(line))).toEqual([]);
});
