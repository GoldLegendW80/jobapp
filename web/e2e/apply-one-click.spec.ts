import {
  expect,
  test,
  type Browser,
  type BrowserContext,
  type Locator,
  type Page,
} from "@playwright/test";

import { waitForPostingDocuments } from "./apply-document-helpers";
import { stub } from "./apply-helpers";
import { setReviewBeforeSend } from "./apply-one-click-helpers";
import {
  CSRF,
  addPosting,
  caddyUrl,
  invented,
  signUp,
  waitForRender,
  type Person,
} from "./documents-helpers";
import { clearAttempts, newContext, watchErrors } from "./stack";

/**
 * One click Apply (issue #738, then A19) against the real stack through Caddy plus the fake ATS
 * forms server. The owner's words: "when I click on apply, it open a browser, navigate to the job
 * offer, fill in the form and click on apply. I want a checkbox in settings to set if I want to
 * review the form filled. By default its no (full auto)"; then (2026-11-05) "do it exactly like
 * Mac: Claude Code + Playwright CLI. no screenshot, no guardrails, submit without asking. only
 * asking if the agent do not know what to fill", and "The agent clicks it"; then (2026-11-06, A20)
 * "I just want that the agent do all the work to apply and he never ask me a question. when I'm
 * not in full auto mode, I want to see all the input he filled in but don't matter of the website
 * or whatever, then a button to confirm."
 *
 * Two invented people with the consent, a base CV and the documents of each posting written by
 * the documents pipeline on the fake backend. The box unticked runs the apply agent on its
 * recorded stream (api/app/agents/fake_streams/apply_agent.json) over the invented job site of
 * tests/ats_forms/a19/: the posting, its Apply link, a two step form (the CV uploaded, a free
 * text answer, the notice period), the agent's own click on Submit application, a confirmation.
 *
 * - the review box unticked, the notice period in the first person's Saved answers: one click on
 *   Apply, no screenshot, no Approve step, and the page says "Sent."; the fake holds exactly one
 *   submission of that form, with the answer and the CV;
 * - the review box ticked (A20): the same agent fills the same site on the review lease and
 *   stops before Submit application; the job page lists every field it filled with one "Confirm
 *   and send", nothing is sent until the click, then the gate sends it once;
 * - the second person's profile lacks the notice period: the agent never asks (A20), it types
 *   its own answer, sends, and the job page shows it under "Answers the assistant chose".
 * - the review box unticked, a posting with no CV nor letter, Apply on its Jobs list row (owner,
 *   2026-11-05: "Apply with no documents writes them and then sends; Apply also appears on Jobs
 *   list rows"): one click from the list, the CV and letter written by the documents pipeline's
 *   default fake streams, then the agent fills the form and sends it, exactly once;
 * - Apply to the selected postings (owner, 2026-11-06: "in the job list and map, i want being
 *   able with the multi select to apply at all selected"): three postings ticked on the Jobs
 *   list, one confirmation that names the two it applies to and leaves out the third, marked as
 *   applied, with its reason; one click on "Apply to 2", the selection cleared, and the agent
 *   sends each of the two exactly once, the third never.
 *
 * Locally, as apply-all.config.ts says (the fake forms compose file, AGENTS_FAKE_BACKEND=1 on
 * the worker, the stack's browser up with COMPOSE_PROFILES=browser), then:
 *   E2E_CADDY_URL=... E2E_ATS_FORMS_URL=... E2E_COMPOSE_PROJECT=... \
 *     pnpm --dir web exec playwright test -c e2e/apply-all.config.ts apply-one-click.spec.ts
 */
// Each agent run is about a dozen acts 3 to 8 seconds apart (A19's pacing) after the documents
// are written; one fill of about 100 s in the Review first test.
test.describe.configure({ mode: "serial", timeout: 1_200_000 });
test.skip(!caddyUrl, "set E2E_CADDY_URL with the Compose stack running");

/** The fake forms server as the stack's browser reaches it (tests/ats-forms-stub.compose.yml). */
const STACK_FORMS = "http://atsforms:8080";
const KEY = `sk-ant-api03-e2egood${"Oc738".repeat(16)}Rn42`;
const FILL_MS = 420_000;
const SEND_MS = 600_000;
/** The posting the recorded stream opens, and the answer it types once it was given. The
 * stream opens this address whatever the posting's: a query (`?job=`) makes another form address
 * for each posting of one person, so the duplicate rules see different forms. */
const AGENT_SITE = `${STACK_FORMS}/forms/a19/posting.html`;
const QUESTION = "What is your notice period?";
const ANSWER = "Six weeks";
/** What the stream types when nothing is saved: the agent's own answer (A20). */
const CHOSEN = "One month";

type Who = {
  person: Person;
  context: BrowserContext;
  page: Page;
  errors: string[];
};
let first: Who;
let second: Who;
/** Everyone `ready` made, for the cleanup even when a later step failed. */
const people: Who[] = [];

/** A posting of `who` at `url`, with its CV and letter written unless `documents` is false.
 * The documents tick writes nothing by itself (documents D29), and one click Apply never reads a
 * fit threshold. */
async function postingOn(
  who: Who,
  url: string,
  suffix: string,
  documents = true,
  fit = 90,
): Promise<string> {
  const { page, person } = who;
  const job = await addPosting(page.request, {
    ...person,
    posting: { ...person.posting, title: person.posting.title + suffix },
  });
  const read = await page.request.get(`/api/jobs/${job}`);
  const { updated_at } = (await read.json()) as { updated_at: string };
  const patched = await page.request.patch(`/api/jobs/${job}`, {
    headers: CSRF,
    data: { url, fit, updated_at },
  });
  expect(patched.status()).toBe(200);
  if (!documents) return job;
  const written = await page.request.post(`/api/jobs/${job}/documents/write`, {
    headers: CSRF,
    data: { kinds: ["cv", "letter"], script: "journey" },
  });
  expect(written.status()).toBe(202);
  await waitForPostingDocuments(page.request, job);
  return job;
}

/** The submissions the fake recorded for the A19 site's form, in order. */
async function agentSent() {
  return (await stub.submissions()).filter(
    (one) => one.ats === "custom" && one.org === "a19" && one.accepted,
  );
}

type Application = { id: string; state: string; mode: string };
async function latest(who: Who, job: string): Promise<Application> {
  const response = await who.page.request.get(
    `/api/applications?posting_id=${job}&limit=1`,
  );
  const { items } = (await response.json()) as { items: Application[] };
  return items[0] ?? { id: "", state: "absent", mode: "" };
}

/** An invented person signed up, with a model key, the consent and a base CV. */
async function ready(browser: Browser, tag: string): Promise<Who> {
  const person = invented(tag);
  const context = await newContext(browser);
  const page = await context.newPage();
  const errors = watchErrors(page);
  await signUp(page, person);
  const saved = await page.request.put("/api/me/model-credential", {
    headers: CSRF,
    data: { kind: "anthropic_api_key", secret: KEY },
  });
  expect(saved.status()).toBe(200);
  const asked = await page.request.get("/api/documents/consent");
  const { version } = (await asked.json()) as { version: number };
  const accepted = await page.request.post("/api/documents/consent", {
    headers: CSRF,
    data: { version },
  });
  expect(accepted.status()).toBe(200);
  const built = await page.request.post("/api/documents/base-cvs", {
    headers: CSRF,
    data: { from: "profile", script: "documents_base_journey" },
  });
  expect(built.status()).toBe(202);
  const base = ((await built.json()) as { base_cv: { id: string } }).base_cv;
  await waitForRender(page.request, base.id, 1);
  const who = { person, context, page, errors };
  people.push(who);
  return who;
}

/** Apply on the job page with the box unticked, until the application is `direct`. */
async function applyNow(who: Who, job: string) {
  await who.page.goto(`/jobs/${job}`);
  const section = who.page.getByRole("region", { name: "Your application" });
  await expect(section).toContainText(
    "The assistant fills the form and sends it. You can watch it live here.",
  );
  await section.getByRole("button", { name: "Apply", exact: true }).click();
  await expect
    .poll(async () => (await latest(who, job)).mode, { timeout: 30_000 })
    .toBe("direct");
  return section;
}

/** What the agent sent, as the stream types it, and nothing a screenshot would show. */
async function sentByTheAgent(
  who: Who,
  job: string,
  section: Locator,
  notice = ANSWER,
  mode = "direct",
) {
  await expect(
    section.getByRole("status").filter({ hasText: /^Sent\./ }),
  ).toBeVisible({ timeout: FILL_MS + SEND_MS });
  expect(await latest(who, job)).toMatchObject({ state: "submitted", mode });
  await expect(
    section.getByRole("button", { name: "Approve and submit" }),
  ).toHaveCount(0);
  await expect(section.getByRole("img", { name: /the click/ })).toHaveCount(0);
  const last = (await agentSent()).at(-1);
  expect(last?.fields).toMatchObject({
    email: "e2e-a19@example.org",
    notice,
  });
  expect(last?.files.map((file) => [file.field, file.filename])).toEqual([
    ["resume", "cv.pdf"],
  ]);
}

test.beforeAll(async ({ browser }) => {
  await stub.reset();
  first = await ready(browser, "oneclick");
  second = await ready(browser, "oneclickask");
});

test.afterAll(async () => {
  for (const who of people) await who.context.close();
  clearAttempts(people.map((who) => who.person.email));
});

test("a new account has the review box unticked", async () => {
  const settings = await first.page.request.get("/api/apply/settings");
  expect(
    ((await settings.json()) as { review_before_send: boolean })
      .review_before_send,
  ).toBe(false);
});

test("box unticked: the agent fills both steps and clicks Submit itself, exactly once", async () => {
  // The notice period is one of the person's Saved answers, so the agent never asks.
  const kept = await first.page.request.post("/api/apply/answer-bank", {
    headers: CSRF,
    data: { question: QUESTION, answer: ANSWER },
  });
  expect(kept.status()).toBe(201);
  await setReviewBeforeSend(first.page.request, false);
  const job = await postingOn(first, AGENT_SITE, ", Fleet");
  const section = await applyNow(first, job);
  await sentByTheAgent(first, job, section);
  await expect(first.page.getByTestId("agent-question")).toHaveCount(0);
  expect(await agentSent()).toHaveLength(1);
});

test("box ticked: the agent fills, the page lists every field, Confirm sends it once", async () => {
  const { page } = first;
  await setReviewBeforeSend(page.request, true);
  const job = await postingOn(first, AGENT_SITE, ", Controls");
  await page.goto(`/jobs/${job}`);
  const section = page.getByRole("region", { name: "Your application" });
  await expect(section).toContainText(
    "The assistant fills the form, then you check it and send it.",
  );
  await section.getByRole("button", { name: "Apply", exact: true }).click();
  const confirm = section.getByRole("button", {
    name: "Confirm and send",
    exact: true,
  });
  await expect(confirm).toBeVisible({ timeout: FILL_MS });
  expect(await latest(first, job)).toMatchObject({
    state: "ready",
    mode: "review",
  });
  // Every field the agent filled, from its own acts, with the value it gave.
  const list = section.getByRole("region", {
    name: "What the assistant filled",
  });
  const shown: [string, string][] = [
    ["First name", "Adaline"],
    ["Email", "e2e-a19@example.org"],
    ["Resume", "cv.pdf"],
    [QUESTION, ANSWER],
  ];
  for (const [label, value] of shown)
    await expect(
      list.getByRole("row").filter({ hasText: label }).first(),
    ).toContainText(value);
  await expect(
    section.getByRole("button", { name: "Approve and submit" }),
  ).toHaveCount(0);
  await expect(page.getByTestId("agent-question")).toHaveCount(0);
  // Nothing leaves before the Confirm: the agent's submission is still the only one.
  expect(await agentSent()).toHaveLength(1);
  await confirm.click();
  await sentByTheAgent(first, job, section, ANSWER, "review");
  expect(await agentSent()).toHaveLength(2);
});

test("the profile lacks an answer: the agent never asks, chooses one and sends", async () => {
  const { page } = second;
  await setReviewBeforeSend(page.request, false);
  const job = await postingOn(second, AGENT_SITE, ", Arms");
  const section = await applyNow(second, job);
  await sentByTheAgent(second, job, section, CHOSEN);
  await expect(page.getByTestId("agent-question")).toHaveCount(0);
  const chosen = section.getByRole("region", {
    name: "Answers the assistant chose",
  });
  await expect(chosen).toContainText(QUESTION);
  await expect(chosen).toContainText(CHOSEN);
  expect(await agentSent()).toHaveLength(3);
});

test("box unticked, no documents: Apply on the Jobs list row writes them, then the agent sends", async () => {
  const { page, person } = first;
  await setReviewBeforeSend(page.request, false);
  const sent = (await agentSent()).length;
  const job = await postingOn(
    first,
    `${AGENT_SITE}?job=list`,
    ", Simulation",
    false,
    40,
  );
  const title = person.posting.title + ", Simulation";
  await page.goto("/jobs");
  await page
    .getByRole("button", {
      name: `Apply to ${title} at ${person.posting.company}`,
    })
    .click();
  // A confirmation first (owner, 2026-11-06: "yes confirmation"): it names the posting and
  // that its CV and letter are written first; its Apply to 1 starts it.
  const confirm = page.getByRole("alertdialog", {
    name: "Apply to 1 posting?",
  });
  await expect(confirm).toContainText(`${person.posting.company}, ${title}`);
  await confirm.getByRole("button", { name: "Apply to 1" }).click();
  await confirm.getByRole("button", { name: "Close" }).click();
  // Still on the list: the row links to the application under way.
  await expect(
    page.getByRole("link", {
      name: `Applying: the application to ${title} at ${person.posting.company}`,
    }),
  ).toBeVisible({ timeout: 30_000 });
  const started = await latest(first, job);
  expect(started.mode).toBe("direct");
  const detail = await page.request.get(`/api/applications/${started.id}`);
  const { events } = (await detail.json()) as {
    events: { kind: string; data: Record<string, unknown> }[];
  };
  expect(events.find((one) => one.kind === "documents_asked")?.data).toEqual({
    kinds: ["cv", "letter"],
  });

  await page.goto(`/jobs/${job}`);
  const section = page.getByRole("region", { name: "Your application" });
  await sentByTheAgent(first, job, section);
  await waitForPostingDocuments(page.request, job);
  expect(await agentSent()).toHaveLength(sent + 1);
  await expect(section).not.toContainText(/[\u2013\u2014]| - /);
});

test("Apply to the selected postings: one confirmation, each sent once, one left out with its reason", async () => {
  const { page, person } = first;
  await setReviewBeforeSend(page.request, false);
  const sent = (await agentSent()).length;
  const suffixes = [", Bulk One", ", Bulk Two", ", Bulk Done"] as const;
  const jobs = [
    await postingOn(first, `${AGENT_SITE}?job=bulk1`, suffixes[0]),
    await postingOn(first, `${AGENT_SITE}?job=bulk2`, suffixes[1]),
  ];
  const done = await postingOn(
    first,
    `${AGENT_SITE}?job=bulk3`,
    suffixes[2],
    false,
    40,
  );
  // The third is marked as applied by hand: its own Apply would refuse it.
  const read = await page.request.get(`/api/jobs/${done}`);
  const { updated_at } = (await read.json()) as { updated_at: string };
  const marked = await page.request.patch(`/api/jobs/${done}`, {
    headers: CSRF,
    data: { status: "applied", updated_at },
  });
  expect(marked.status()).toBe(200);
  const titles = suffixes.map((suffix) => person.posting.title + suffix);
  const company = person.posting.company;

  await page.goto("/jobs");
  for (const title of titles)
    await page
      .getByRole("checkbox", { name: `Select ${title} at ${company}` })
      .check();
  const bar = page.getByRole("region", { name: "Bulk actions" });
  await expect(bar).toContainText("3 postings selected");
  await bar.getByRole("button", { name: "Apply", exact: true }).click();
  const dialog = page.getByRole("alertdialog", {
    name: "Apply to 2 postings?",
  });
  await expect(dialog).toContainText(
    "The assistant fills each form and sends it. Your click approves exactly the postings listed here.",
  );
  const listed = dialog.getByRole("list", { name: "Postings to apply to" });
  await expect(listed.getByRole("listitem")).toHaveCount(2);
  for (const title of titles.slice(0, 2))
    await expect(listed).toContainText(
      `${company}, ${title}CV and letter ready.`,
    );
  const left = dialog.getByRole("list", { name: "Not applied to (1)" });
  await expect(left).toContainText(
    `${company}, ${titles[2]}This posting is marked as applied.`,
  );
  await expect(dialog).not.toContainText(/[\u2013\u2014]| - /);
  await dialog.getByRole("button", { name: "Apply to 2" }).click();

  const summary = page.getByRole("alertdialog", {
    name: "Applying to 2 postings",
  });
  await expect(summary.getByRole("status")).toContainText(
    "Applying to 2 postings.",
  );
  await expect(
    summary.getByRole("link", { name: "Open Applications" }),
  ).toBeVisible();
  await summary.getByRole("button", { name: "Close" }).click();
  // The selection is cleared, and the rows show the applications under way.
  await expect(bar).toHaveCount(0);
  for (const title of titles.slice(0, 2))
    await expect(
      page.getByRole("link", {
        name: `Applying: the application to ${title} at ${company}`,
      }),
    ).toBeVisible({ timeout: 30_000 });

  // The fills go one after the other: each is sent by the agent, exactly once.
  for (const job of jobs)
    await expect
      .poll(async () => (await latest(first, job)).state, {
        timeout: 2 * (FILL_MS + SEND_MS),
      })
      .toBe("submitted");
  expect(await agentSent()).toHaveLength(sent + 2);
  expect(await latest(first, done)).toMatchObject({ state: "absent" });
});

test("no page error was logged", () => {
  expect(people.flatMap((who) => who.errors)).toEqual([]);
});
