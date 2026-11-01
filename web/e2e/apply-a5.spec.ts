import { expect, test, type BrowserContext, type Page } from "@playwright/test";

import { waitForPostingDocuments } from "./apply-document-helpers";
import { atsFormsUrl, stub } from "./apply-helpers";
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
import { reviewFirst } from "./apply-one-click-helpers";

/**
 * Apply A5, the answer agent, against the real stack through Caddy plus the fake ATS forms
 * server. An invented person with a model credential (a made up key: the worker runs the fake
 * backend), the documents consent, a base CV and a posting whose address is the fake Ashby form
 * has its CV and letter written on the fake backend, ticks the review box and clicks Apply, and the fill
 * runs on the worker. The fake Ashby form asks an open question ("Why Example Robotics?"): the
 * `apply_answer` agent answers it in a run of the agents runtime (the recorded stream
 * `apply_answer`), the judge keeps it because the person's motivation says it, and the approval
 * screen shows it as the assistant's answer, which the person can still edit. Nothing is
 * submitted: the fake server receives no submission.
 *
 * Locally, as apply-all.config.ts says (the fake forms compose file, AGENTS_FAKE_BACKEND=1 on
 * the worker, the stack's browser up), then:
 *   E2E_CADDY_URL=... E2E_ATS_FORMS_URL=... E2E_COMPOSE_PROJECT=... \
 *     pnpm --dir web exec playwright test -c e2e/apply-all.config.ts apply-a5.spec.ts
 */
test.describe.configure({ mode: "serial", timeout: 480_000 });
test.skip(!caddyUrl, "set E2E_CADDY_URL with the Compose stack running");

const WHY = "Why Example Robotics?";
/** What the recorded stream `apply_answer` submits for the first question it is asked. */
const ANSWERED =
  "The robots this team builds are the kind of work I want to do, and this role puts agents in front of real operations. " +
  "I would bring my agent and evaluation work to the team.";
const EDITED = "I want to build agents for robots that leave the lab.";
/** The fake forms server as the stack's browser reaches it (tests/ats-forms-stub.compose.yml). */
const STACK_FORMS = "http://atsforms:8080";
const KEY = `sk-ant-api03-e2egood${"Ka5x".repeat(20)}Rn42`;

let person: Person;
let context: BrowserContext;
let page: Page;
let errors: string[];
let job: string;

test.beforeAll(async ({ browser }) => {
  person = invented("a5");
  // The judge keeps only a sentence the person's own words support (an unsourced reply is
  // refused): the motivation holds the recorded stream's statements, so both are kept.
  const profile = person.profile as {
    sections: { answers: { motivation: string } };
  };
  profile.sections.answers.motivation = ANSWERED;
  context = await newContext(browser);
  page = await context.newPage();
  errors = watchErrors(page);
});

test.afterAll(async () => {
  await context.close();
  clearAttempts([person.email]);
});

test("an invented person has a credential, the consent, a base CV and an Ashby posting", async () => {
  await stub.reset();
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

  job = await addPosting(page.request, person);
  const posting = (await stub.posting("ashby")).replace(
    atsFormsUrl ?? "",
    STACK_FORMS,
  );
  const read = await page.request.get(`/api/jobs/${job}`);
  const { updated_at } = (await read.json()) as { updated_at: string };
  const patched = await page.request.patch(`/api/jobs/${job}`, {
    headers: CSRF,
    data: { url: posting, fit: 90, updated_at },
  });
  expect(patched.status()).toBe(200);
  const written = await page.request.post(`/api/jobs/${job}/documents/write`, {
    headers: CSRF,
    data: { kinds: ["cv", "letter"], script: "journey" },
  });
  expect(written.status()).toBe(202);
  await waitForPostingDocuments(page.request, job);
});

test("the open question is answered by the agent, shown as its answer, and editable", async () => {
  await page.goto(`/jobs/${job}`);
  const section = page.getByRole("region", { name: "Apply" });
  await reviewFirst(page.request);
  await section.getByRole("button", { name: "Apply", exact: true }).click();

  // The worker's tick starts the fill within 30 seconds; the answer agent runs inside it.
  await expect(
    section.getByRole("img", { name: /before the click/ }),
  ).toBeVisible({ timeout: 240_000 });

  const row = section.locator("li").filter({ hasText: WHY });
  await expect(row).toHaveCount(1);
  await expect(row).toContainText(ANSWERED);
  await expect(row).not.toHaveAttribute("data-left", "true");
  await expect(row.getByText("Assistant", { exact: true })).toBeVisible();

  // The person keeps the last word: the agent's answer opens for editing and the saved text is
  // theirs.
  await row.getByRole("button", { name: `Edit: ${WHY}` }).click();
  const box = row.getByRole("textbox");
  await expect(box).toHaveValue(new RegExp(`^${ANSWERED.slice(0, 40)}`));
  await box.fill(EDITED);
  await row.getByRole("button", { name: "Save answer" }).click();
  await expect(row).toContainText(EDITED);
  await expect(row.getByText("You", { exact: true })).toBeVisible();

  // The fill staged the posting and sent nothing.
  const read = await page.request.get(`/api/jobs/${job}`);
  expect(((await read.json()) as { status: string }).status).toBe("staged");
  expect(await stub.submissions()).toHaveLength(0);
});

test("no page error was logged", () => {
  expect(errors).toEqual([]);
});
