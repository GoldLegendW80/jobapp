import { execFileSync } from "node:child_process";
import { randomBytes } from "node:crypto";

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

const shots = makeShots("agents-a2", "runs");
const costShots = makeShots("agents-33-streaming", "cost");

/**
 * The agent run panel and /runs (agents A2) against the real stack through Caddy, with the
 * worker replaying recorded streams (AGENTS_FAKE_BACKEND, tests/compose.a2-e2e.yml): no model is
 * ever called. A test run streams step by step; a reload mid run and a network loss both resume
 * the stream with no step lost or shown twice; another run is cancelled; the first one ends with
 * its tokens and cost; a run trying forbidden tools shows them refused.
 *
 *   HTTP_PORT=8127 docker compose -p jobapp-a2 -f compose.yml -f compose.mac.yml -f tests/anthropic-stub.compose.yml \
 *     -f tests/compose.a2-e2e.yml up -d --build --wait
 *   docker compose -p jobapp-a2 -f compose.yml exec -T api alembic upgrade head
 *   E2E_CADDY_URL=http://127.0.0.1:8127 E2E_COMPOSE_PROJECT=jobapp-a2 \
 *     pnpm --dir web exec playwright test agents-runs.spec.ts
 *   docker compose -p jobapp-a2 -f compose.yml -f tests/anthropic-stub.compose.yml \
 *     -f tests/compose.a2-e2e.yml down -v
 */
test.describe.configure({ mode: "serial" });
test.skip(!caddyUrl, "set E2E_CADDY_URL with the Compose stack running");
test.setTimeout(120_000);

const run = Date.now().toString(36);
const ADMIN = process.env.E2E_ADMIN_EMAIL ?? `e2e-runs-${run}@example.org`;
const PASSWORD = `Pw ${randomBytes(12).toString("base64url")}`;
const tail = () => randomBytes(30).toString("base64url").replace(/[-_]/g, "x");
// A made up key of the right shape: the fake backend never sends it anywhere.
const KEY = `sk-ant-api03-e2egood${tail()}${tail()}Rn42`;

let context: BrowserContext;
let page: Page;
let errors: string[];
let firstRun = "";

const panel = () => page.getByTestId("run-panel");
const steps = () => panel().getByRole("list", { name: "Steps" });
const badge = () => panel().locator("[data-slot=badge]");

/** The "Step N of 30" answers shown, each must appear once. */
async function slowSteps(): Promise<number[]> {
  const texts = await steps().locator("li[data-kind=text]").allInnerTexts();
  return texts
    .map((t) => /Step (\d+) of 30/.exec(t)?.[1])
    .filter((n): n is string => n !== undefined)
    .map(Number);
}

async function expectNoDuplicates() {
  const seen = await slowSteps();
  expect(new Set(seen).size, `steps: ${seen.join(",")}`).toBe(seen.length);
  expect(seen).toEqual([...seen].sort((a, b) => a - b));
  return seen;
}

async function startTestRun(script: string, message: string) {
  await page.goto("/runs");
  await page.getByLabel("Recorded stream").selectOption(script);
  await page.getByLabel("Message").fill(message);
  await page.getByRole("button", { name: "Start test run" }).click();
  await expect(page).toHaveURL(/\/runs\/[0-9a-f-]{36}$/);
  return page.url().split("/").at(-1) ?? "";
}

test.beforeAll(async ({ browser }) => {
  context = await newContext(browser);
  page = await context.newPage();
  errors = watchErrors(page);
});

test.afterAll(async () => {
  await context.close();
  clearAttempts([ADMIN]);
});

test("the runs page is empty and offers a test run", async () => {
  const link = cliLink(ADMIN);
  await page.goto(link);
  await setPassword(
    page,
    PASSWORD,
    link.startsWith("/invite/") ? "E2E Runs" : undefined,
  );
  const saved = await page.request.put("/api/me/model-credential", {
    headers: { "X-Requested-With": "jobapp" },
    data: { kind: "anthropic_api_key", secret: KEY },
  });
  expect(saved.status()).toBe(200);
  await page.getByRole("link", { name: "Runs" }).click();
  await expect(page.getByRole("heading", { name: "Agent runs" })).toBeVisible();
  await expect(page.getByText(/No run yet/)).toBeVisible();
  await expect(page.getByText("Test run", { exact: true })).toBeVisible();
  await expect(page.locator("body")).not.toContainText(/[–—]| - /);
  await shots(page, "empty");
});

test("a test run streams its steps live", async () => {
  firstRun = await startTestRun("slow", "the A2 journey");
  await expect(panel().getByText("Live")).toBeVisible();
  await expect(badge()).toHaveText("Running");
  await expect(steps().getByText("Started on claude-opus-5-5")).toBeVisible();
  await expect(steps().getByText(/Step 3 of 30/)).toBeVisible({
    timeout: 15_000,
  });
  await expectNoDuplicates();
  await shots(page, "streaming", false);
});

test("a reload mid run resumes the stream without a gap or a duplicate", async () => {
  const before = await expectNoDuplicates();
  await page.reload();
  await expect(panel()).toBeVisible();
  // The replay brings back every step already written, then the new ones keep coming.
  await expect(steps().getByText(`Step ${before.at(-1)} of 30`)).toBeVisible();
  await expect(
    steps().getByText(`Step ${(before.at(-1) ?? 0) + 2} of 30`),
  ).toBeVisible({ timeout: 15_000 });
  const after = await expectNoDuplicates();
  expect(after.slice(0, before.length)).toEqual(before);
  expect(after[0]).toBe(1);
});

test("a network loss shows reconnecting, then resumes where it stopped", async () => {
  // Offline keeps the browser from reconnecting; restarting caddy cuts the open stream (an
  // offline browser keeps an established connection alive otherwise).
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
  await shots(page, "reconnecting", false);
  const during = await expectNoDuplicates();
  await page.waitForTimeout(3000);
  await context.setOffline(false);
  await expect(panel().getByText("Live")).toBeVisible({ timeout: 30_000 });
  await expect(
    steps().getByText(`Step ${(during.at(-1) ?? 0) + 4} of 30`),
  ).toBeVisible({ timeout: 20_000 });
  const after = await expectNoDuplicates();
  // Every step from 1 on, consecutive: nothing lost while offline.
  expect(after).toEqual(after.map((_, i) => i + 1));
});

test("another run is cancelled from its panel", async () => {
  await startTestRun("slow", "to be cancelled");
  await expect(steps().getByText(/Step 2 of 30/)).toBeVisible({
    timeout: 15_000,
  });
  await panel().getByRole("button", { name: "Cancel" }).click();
  await expect(badge()).toHaveText("Cancelled", { timeout: 15_000 });
  await expect(steps().locator("li[data-kind=status]").last()).toContainText(
    "Cancelled",
  );
  await expect(
    panel().getByRole("button", { name: /Cancel|Stopping/ }),
  ).toHaveCount(0);
  await expect(panel().getByText("Ended")).toBeVisible();
  const seen = await expectNoDuplicates();
  expect(seen.length).toBeLessThan(30);
  await shots(page, "cancelled");
});

test("the first run ends with its tokens, cost and duration", async () => {
  await page.goto(`/runs/${firstRun}`);
  await expect(badge()).toHaveText("Done", { timeout: 60_000 });
  const seen = await expectNoDuplicates();
  expect(seen).toEqual(Array.from({ length: 30 }, (_, i) => i + 1));
  const metrics = panel().locator("dl").first();
  await expect(metrics).toContainText("13,600 in, 360 out");
  await expect(metrics).toContainText("$0.25");
  await expect(panel().getByText("Ended")).toBeVisible();
  await shots(page, "done");
});

test("forbidden tools are refused in the timeline", async () => {
  await startTestRun("forbidden", "try the forbidden tools");
  await expect(badge()).toHaveText("Done", { timeout: 15_000 });
  for (const name of [
    "Bash",
    "Write",
    "WebFetch",
    "Read",
    "mcp__other__steal",
  ]) {
    await expect(steps().getByText(`Refused: ${name}`)).toBeVisible();
  }
  await shots(page, "refused");
});

test("the runs page lists every run with its status, tokens and cost", async () => {
  await page.goto("/runs");
  const list = page.getByRole("list", { name: "Runs" });
  await expect(list.getByRole("link")).toHaveCount(3);
  await expect(
    list.getByRole("link").filter({ hasText: "Cancelled" }),
  ).toHaveCount(1);
  await expect(list.getByRole("link").filter({ hasText: "Done" })).toHaveCount(
    2,
  );
  await expect(list).toContainText("$0.25");
  await shots(page, "list");
  // The deliberate network loss leaves the browser's own lines about the cut stream.
  const cut =
    /net::ERR_[A-Z_]+ @ .*\/api\/agents\/runs\/[0-9a-f-]+\/stream\?after=\d+$/;
  expect(errors.filter((line) => !cut.test(line))).toEqual([]);
});

test("a run on a subscription shows an estimate that is not a bill, a run on an API key its cost", async () => {
  // The run that ended with an API key: its amount is a cost, in its panel and in the list.
  await page.goto(`/runs/${firstRun}`);
  const metrics = () => panel().locator("dl").first();
  await expect(metrics()).toContainText("Cost$0.25");
  await expect(panel().getByTestId("cost-note")).toHaveCount(0);
  await expect(steps().locator("li[data-kind=usage]")).toContainText(
    /out, \$0\.25$/,
  );
  await costShots(page, "cost-api-key");

  // The same user now saves a subscription token (made up; the fake backend sends it nowhere).
  const token = `sk-ant-oat01-e2egood${tail()}${tail()}Sub1`;
  const saved = await page.request.put("/api/me/model-credential", {
    headers: { "X-Requested-With": "jobapp" },
    data: { kind: "claude_subscription", secret: token },
  });
  expect(saved.status()).toBe(200);
  const id = await startTestRun("echo", "what does a subscription run cost");
  await expect(badge()).toHaveText("Done", { timeout: 30_000 });
  await expect(metrics()).toContainText("Estimate at API prices$0.0123");
  await expect(metrics()).not.toContainText("Cost");
  await expect(panel().getByTestId("cost-note")).toHaveText(
    "This run used your Claude subscription and counts toward your plan's usage limits. What you pay is set by your plan, not by this amount: the estimate is what the same tokens cost at API prices.",
  );
  await expect(steps().locator("li[data-kind=usage]")).toContainText(
    "$0.0123 estimated at API prices, not a bill",
  );
  // Issue #80: the note is the description of the amount for assistive technology.
  await expect(
    metrics().locator("dd", { hasText: "$0.0123" }),
  ).toHaveAccessibleDescription(/counts toward your plan's usage limits/);
  await expect(page.locator("body")).not.toContainText(/[–—]| - /);
  await costShots(page, "cost-subscription");
  // The api says which kind each run used, and the earlier run kept its own.
  const kinds = await Promise.all(
    [id, firstRun].map(async (run) => {
      const detail = await page.request.get(`/api/agents/runs/${run}`);
      return ((await detail.json()) as { credential_kind: string })
        .credential_kind;
    }),
  );
  expect(kinds).toEqual(["claude_subscription", "anthropic_api_key"]);
  // Issue #80: the usage step itself says which kind, next to the amount, never the secret.
  const stored = (await (
    await page.request.get(`/api/agents/runs/${id}`)
  ).json()) as { events: { kind: string; data: Record<string, unknown> }[] };
  const usage = stored.events.filter((event) => event.kind === "usage");
  expect(usage.map((event) => event.data.credential_kind)).toEqual([
    "claude_subscription",
  ]);
  expect(JSON.stringify(stored)).not.toContain(token);

  await page.goto("/runs");
  const list = page.getByRole("list", { name: "Runs" });
  await expect(list.getByRole("link")).toHaveCount(4);
  const costs = list.getByTestId("run-cost");
  await expect(costs.first()).toHaveText("$0.0123Estimate, not a bill");
  await expect(costs.filter({ hasText: "Estimate, not a bill" })).toHaveCount(
    1,
  );
  const intro = page.getByText(
    /On a Claude subscription the amount is an estimate/,
  );
  await expect(intro).toBeVisible();
  await expect(intro).toContainText(
    "not a bill: each run counts toward your plan's usage limits, and what you pay is set by your plan.",
  );
  await expect(page.locator("body")).not.toContainText(/not billed/i);
  await costShots(page, "cost-list");
});
