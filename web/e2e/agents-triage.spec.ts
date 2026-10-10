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

const shots = makeShots("agents-a3", "triage");

/**
 * The job triage agent on job detail (agents A3) against the real stack through Caddy, the worker
 * replaying the recorded triage stream (AGENTS_FAKE_BACKEND, no model call): Assess with agent
 * is disabled without a credential; with one, the run streams live in the panel, the proposals
 * appear inline, the note is accepted (the notes and the history show it), the status change is
 * rejected (the status stays), a second assessment's note is edited then accepted, and /runs
 * links the runs to the job.
 *
 *   HTTP_PORT=8127 docker compose -p jobapp-a3 -f compose.yml -f tests/compose.a2-e2e.yml \
 *     up -d --build --wait
 *   docker compose -p jobapp-a3 -f compose.yml exec -T api alembic upgrade head
 *   E2E_CADDY_URL=http://127.0.0.1:8127 E2E_COMPOSE_PROJECT=jobapp-a3 \
 *     pnpm --dir web exec playwright test agents-triage.spec.ts
 *   docker compose -p jobapp-a3 -f compose.yml -f tests/compose.a2-e2e.yml down -v --rmi local
 */
test.describe.configure({ mode: "serial" });
test.skip(!caddyUrl, "set E2E_CADDY_URL with the Compose stack running");
test.setTimeout(120_000);

const run = Date.now().toString(36);
const EMAIL = `e2e-triage-${run}@example.org`;
const PASSWORD = `Pw ${randomBytes(12).toString("base64url")}`;
const tail = () => randomBytes(30).toString("base64url").replace(/[-_]/g, "x");
// A made up key of the right shape: the fake backend never sends it anywhere.
const KEY = `sk-ant-api03-e2etriage${tail()}${tail()}Tr3a`;
const CSRF = { "X-Requested-With": "jobapp" };
const NOTE_START = "Strong fit: the posting asks for agent orchestration";

let context: BrowserContext;
let page: Page;
let errors: string[];
let jobUrl = "";

const section = () => page.getByRole("region", { name: "Agent assessment" });
const panel = () => section().getByTestId("run-panel");
const badge = () => panel().locator("[data-slot=badge]");
const steps = () => panel().getByRole("list", { name: "Steps" });
const proposals = () => section().getByRole("list", { name: "Proposals" });
const pending = (kind: string) =>
  proposals().locator(`[data-testid=proposal][data-kind=${kind}]`, {
    has: page.getByText("Pending", { exact: true }),
  });
const history = () => page.getByRole("list", { name: "History, newest first" });

test.beforeAll(async ({ browser }) => {
  context = await newContext(browser);
  page = await context.newPage();
  errors = watchErrors(page);
});

test.afterAll(async () => {
  await context.close();
  clearAttempts([EMAIL]);
});

test("without a credential, Assess with agent is disabled and says why", async () => {
  const link = cliLink(EMAIL);
  await page.goto(link);
  await setPassword(
    page,
    PASSWORD,
    link.startsWith("/invite/") ? "E2E Triage" : undefined,
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
  await page.goto(jobUrl);
  await expect(section()).toBeVisible();
  const button = section().getByRole("button", { name: "Assess with agent" });
  await expect(button).toBeDisabled();
  await expect(section().getByText(/none is saved yet/)).toBeVisible();
  await expect(
    section().getByRole("link", { name: "Add one on the Account page" }),
  ).toHaveAttribute("href", "/account");
  await expect(page.locator("body")).not.toContainText(/[–—]| - /);
  await section().scrollIntoViewIfNeeded();
  await shots(page, "no-credential", false);
});

test("Assess with agent streams the run live, then lists its proposals", async () => {
  const saved = await page.request.put("/api/me/model-credential", {
    headers: CSRF,
    data: { kind: "anthropic_api_key", secret: KEY },
  });
  expect(saved.status()).toBe(200);
  await page.reload();
  const button = section().getByRole("button", { name: "Assess with agent" });
  await expect(button).toBeEnabled();
  await button.click();
  await expect(panel()).toBeVisible();
  await expect(
    section().getByRole("button", { name: "Assessing" }),
  ).toBeDisabled();
  await expect(badge()).toHaveText(/Queued|Running/);
  await expect(steps().getByText("Started on claude-opus-5-5")).toBeVisible({
    timeout: 15_000,
  });
  await expect(steps().getByText("Tool call: get_job")).toBeVisible({
    timeout: 15_000,
  });
  await section().scrollIntoViewIfNeeded();
  await shots(page, "streaming", false);
  await expect(badge()).toHaveText("Done", { timeout: 30_000 });
  await expect(steps().getByText("Tool call: propose_note")).toBeVisible();
  await expect(steps().getByText("Tool call: propose_status")).toBeVisible();
  await expect(pending("job_note")).toContainText(NOTE_START, {
    timeout: 10_000,
  });
  await expect(pending("job_status")).toContainText(
    "Status: Found to Shortlisted",
  );
  // Nothing changed on the job yet.
  await expect(page.getByLabel("Status", { exact: true })).toHaveValue("found");
  await expect(
    page.getByRole("textbox", { name: "Notes", exact: true }),
  ).toHaveValue("");
  await shots(page, "proposals");
});

test("accepting the note adds it to the notes and the history", async () => {
  await pending("job_note")
    .getByRole("button", { name: "Accept the note" })
    .click();
  await expect(page.getByText("Note added to the job.")).toBeVisible();
  await expect(
    page.getByRole("textbox", { name: "Notes", exact: true }),
  ).toHaveValue(new RegExp(`^${NOTE_START}`));
  await expect(history()).toContainText("Note added (agent proposal accepted)");
  await expect(
    proposals().locator("[data-testid=proposal][data-kind=job_note]"),
  ).toHaveAttribute("data-status", "accepted");
});

test("rejecting the status change leaves the status as it was", async () => {
  await pending("job_status")
    .getByRole("button", { name: "Reject the status change" })
    .click();
  await expect(page.getByText("Proposal rejected.")).toBeVisible();
  await expect(
    proposals().locator("[data-testid=proposal][data-kind=job_status]"),
  ).toHaveAttribute("data-status", "rejected");
  await page.reload();
  await expect(page.getByLabel("Status", { exact: true })).toHaveValue("found");
  await expect(history()).not.toContainText("Status changed");
  await section().scrollIntoViewIfNeeded();
  await shots(page, "decided");
});

test("a second assessment's note is edited, then accepted", async () => {
  await section().getByRole("button", { name: "Assess with agent" }).click();
  await expect(badge()).toHaveText("Done", { timeout: 30_000 });
  const note = pending("job_note");
  await expect(note).toContainText(NOTE_START, { timeout: 10_000 });
  await note
    .getByRole("button", { name: "Edit the note, then accept" })
    .click();
  const box = section().getByLabel("Edit the note");
  await expect(box).toHaveValue(new RegExp(`^${NOTE_START}`));
  await box.fill("Edited by me: a good fit, ask about the on site days.");
  await section().scrollIntoViewIfNeeded();
  await shots(page, "editing", false);
  await section().getByRole("button", { name: "Accept edited note" }).click();
  await expect(page.getByText("Note added to the job.")).toBeVisible();
  await expect(
    page.getByRole("textbox", { name: "Notes", exact: true }),
  ).toHaveValue(/\nEdited by me: a good fit, ask about the on site days\.$/);
  // The second run's status proposal is still pending; the first run's are gone from the list.
  await expect(pending("job_status")).toHaveCount(1);
});

test("/runs links both triage runs to the job", async () => {
  await page.goto("/runs");
  const list = page.getByRole("list", { name: "Runs" });
  const links = list.getByRole("link", {
    name: "Applied AI Engineer at Example Agents",
  });
  await expect(links).toHaveCount(2);
  await expect(links.first()).toHaveAttribute("href", jobUrl);
  await shots(page, "runs");
  await links.first().click();
  await expect(page).toHaveURL(jobUrl);
  expect(errors).toEqual([]);
});
