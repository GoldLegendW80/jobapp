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

const shots = makeShots("agents-a6", "admin-agents");

/**
 * The Agents tab of Admin (agents A6), against the real stack through Caddy: no mocks. The admin
 * opens Admin, Agents, the website search agent, edits its prompt, saves, sees the new version
 * in the history, then reverts it, so the stack's runs are back on the code's definition.
 *
 * Locally, on a scratch Compose project (its own volumes, torn down afterwards):
 *   HTTP_PORT=8095 docker compose -p jobapp-a6 -f compose.yml -f compose.mac.yml up -d --build --wait
 *   docker compose -p jobapp-a6 -f compose.yml exec -T api alembic upgrade head
 *   E2E_CADDY_URL=http://127.0.0.1:8095 E2E_COMPOSE_PROJECT=jobapp-a6 \
 *     pnpm --dir web exec playwright test admin-agents.spec.ts
 *   docker compose -p jobapp-a6 -f compose.yml down -v
 *
 * On the Jetson (E2E_ADMIN_VIA_DEPLOY) it only reads: a saved prompt would reach the real runs of
 * the people using it, so the edit is never made there.
 */
test.describe.configure({ mode: "serial" });
test.skip(!caddyUrl, "set E2E_CADDY_URL with the Compose stack running");

const run = `${Date.now().toString(36)}${randomBytes(2).toString("hex")}`;
const ADMIN = process.env.E2E_ADMIN_EMAIL ?? `e2e-admin-${run}@example.org`;
const PASSWORD = `Pw ${randomBytes(12).toString("base64url")}`;
const AGENT = "browser_discovery";
const LINE = `Journey line ${run}: stay on the website you were given.`;

let context: BrowserContext;
let page: Page;
let errors: string[];

test.beforeAll(async ({ browser }) => {
  context = await newContext(browser);
  page = await context.newPage();
  errors = watchErrors(page);
  const link = cliLink(ADMIN);
  await page.goto(link);
  if (link.startsWith("/invite/"))
    await setPassword(page, PASSWORD, "E2E Admin");
  else await setPassword(page, PASSWORD);
});

test.afterAll(async () => {
  await context.close();
  clearAttempts([ADMIN]);
});

async function openAgent(target: Page, shoot = false) {
  await target.goto("/admin/users");
  await target
    .getByRole("navigation", { name: "Admin" })
    .getByRole("link", { name: "Agents" })
    .click();
  await expect(target).toHaveURL(/\/admin\/agents$/);
  // The list is grouped by category (agents A8): the website search is in Finding jobs.
  const table = target.getByRole("table", { name: "Finding jobs, by name" });
  await expect(table).toBeVisible();
  if (shoot) await shots(target, "agents-list");
  await table.getByRole("link", { name: AGENT, exact: true }).click();
  await expect(
    target.getByRole("heading", { name: AGENT, level: 1 }),
  ).toBeVisible();
}

test("the admin sees everything that makes the website search agent", async () => {
  await openAgent(page, true);
  for (const title of [
    "Identity",
    "System prompt",
    "Tools",
    "How Claude Code is run for it",
  ])
    await expect(
      page.getByRole("heading", { name: title, level: 2 }),
    ).toBeVisible();
  await expect(page.getByLabel("Claude Agent SDK options")).toContainText(
    '"permission_mode": "dontAsk"',
  );
  await expect(
    page.getByText("Whatever a saved prompt says, the code keeps its guards", {
      exact: false,
    }),
  ).toBeVisible();
  await shots(page, "agent-page");
  expect(errors).toEqual([]);
});

test("the admin edits the prompt, saves, sees the version, then reverts", async () => {
  test.skip(
    Boolean(process.env.E2E_ADMIN_VIA_DEPLOY),
    "never edits a real agent on the Jetson",
  );
  await openAgent(page);
  const prompt = page.getByRole("textbox", { name: "System prompt" });
  const before = await prompt.inputValue();
  await prompt.fill(`${before}\n${LINE}`);
  await expect(
    page.getByRole("list", { name: "Changes from the code's prompt" }),
  ).toContainText(LINE);
  await page.getByLabel("Note, optional").fill(`Journey ${run}`);
  await page.getByRole("button", { name: "Save as a new version" }).click();
  const history = page.getByRole("list", { name: "Versions, newest first" });
  const newest = history.getByRole("listitem").first();
  await expect(newest).toContainText("(runs use it)");
  await expect(newest).toContainText("Changes system_prompt.");
  await expect(newest).toContainText(`Journey ${run}`);
  await page.reload();
  await expect(
    page.getByRole("textbox", { name: "System prompt" }),
  ).toHaveValue(`${before}\n${LINE}`);
  await shots(page, "saved-version");

  await page
    .getByRole("button", { name: "Revert to the code's version" })
    .click();
  await page
    .getByRole("alertdialog")
    .getByRole("button", { name: "Revert" })
    .click();
  await expect(history.getByRole("listitem").first()).toContainText(
    "No change: the code's definition.",
  );
  await expect(
    page.getByRole("textbox", { name: "System prompt" }),
  ).toHaveValue(before);
  expect(errors).toEqual([]);
});

test("a signed out visitor is sent to sign in", async ({ browser }) => {
  const other = await newContext(browser);
  const visitor = await other.newPage();
  await visitor.goto(`/admin/agents/${AGENT}`);
  await expect(visitor.getByRole("heading", { name: "Sign in" })).toBeVisible();
  await expect(visitor).toHaveURL(
    /\/login\?next=\/admin\/agents\/browser_discovery$/,
  );
  // The form of the redirected page, which keeps `next` (stack.ts signIn opens /login afresh).
  await visitor.getByLabel("Email").fill(ADMIN);
  await visitor.getByLabel("Password").fill(PASSWORD);
  await visitor.getByRole("button", { name: "Sign in" }).click();
  await expect(visitor).toHaveURL(/\/admin\/agents\/browser_discovery$/);
  await expect(
    visitor.getByRole("heading", { name: AGENT, level: 1 }),
  ).toBeVisible();
  await other.close();
});
