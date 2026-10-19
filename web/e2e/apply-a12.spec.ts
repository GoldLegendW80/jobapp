import {
  expect,
  test,
  type BrowserContext,
  type Locator,
  type Page,
} from "@playwright/test";

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

/**
 * Apply A12 against the real stack through Caddy plus the fake ATS forms server: the review
 * queue. An invented person with the consent, a base CV and two postings whose addresses are the
 * fake Greenhouse and Ashby forms, each with its CV and letter written by the documents pipeline
 * on the fake backend, selects both on the jobs list and clicks Like. Both appear in the queue
 * (/applications) and are filled one after the other. On the Greenhouse card the person sees the
 * documents, answers what was left, edits one answer and approves: the fake server holds exactly
 * one accepted submission, with the edited value, and the item leaves the queue. The Ashby card
 * is rejected with "Skip this posting too" on a phone, by keyboard: nothing is sent, the item
 * leaves the queue and the posting is skipped.
 *
 * Locally, as apply-all.config.ts says (the fake forms compose file, AGENTS_FAKE_BACKEND=1 on
 * the worker, the stack's browser up with COMPOSE_PROFILES=browser), then:
 *   E2E_CADDY_URL=... E2E_ATS_FORMS_URL=... E2E_COMPOSE_PROJECT=... \
 *     pnpm --dir web exec playwright test -c e2e/apply-all.config.ts apply-a12.spec.ts
 */
// Two fills of about 100 s each, one after the other on the one browser, then the submit lease
// (about 120 s), after four documents are written: the budget is the sum of the waits below.
test.describe.configure({ mode: "serial", timeout: 900_000 });
test.skip(!caddyUrl, "set E2E_CADDY_URL with the Compose stack running");

const TYPED = "Not applicable";
const EDITED = "Exampleton Queue";
const PLACE = "Paris, France"; // one of the places of the fake Ashby location typeahead
/** The fake forms server as the stack's browser reaches it (tests/ats-forms-stub.compose.yml). */
const STACK_FORMS = "http://atsforms:8080";
const KEY = `sk-ant-api03-e2egood${"Dn9x".repeat(20)}Rn42`;
const PHONE = { width: 390, height: 844 };

let person: Person;
let context: BrowserContext;
let page: Page;
let errors: string[];
const postings = { greenhouse: "", ashby: "" };
const titles = { greenhouse: "", ashby: "" };

type PostingDocument = { kind: string; latest: { number: number } | null };

async function documentsReady(job: string): Promise<boolean> {
  const response = await page.request.get(`/api/jobs/${job}/documents`);
  const { documents } = (await response.json()) as {
    documents: PostingDocument[];
  };
  return (
    documents.length === 2 && documents.every((one) => one.latest !== null)
  );
}

/** A posting of the person whose address is the fake form of `ats`, with its two documents. */
async function postingOn(ats: "greenhouse" | "ashby"): Promise<string> {
  // Another role at the same company: a Like of the same role while one is live is refused.
  const title =
    person.posting.title + (ats === "ashby" ? ", Developer Tools" : "");
  titles[ats] = title;
  const job = await addPosting(page.request, {
    ...person,
    posting: { ...person.posting, title },
  });
  const posting = (await stub.posting(ats)).replace(
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
  // The fake backend replays the recorded streams of this invented person (apply A7 does the
  // same): a Like then asks for nothing, and the fill starts as soon as the browser is free.
  const written = await page.request.post(`/api/jobs/${job}/documents/write`, {
    headers: CSRF,
    data: { kinds: ["cv", "letter"], script: "journey" },
  });
  expect(written.status()).toBe(202);
  await expect.poll(() => documentsReady(job), { timeout: 150_000 }).toBe(true);
  return job;
}

const cardOf = (ats: "greenhouse" | "ashby"): Locator =>
  page.getByRole("article", { name: titles[ats], exact: true });

/** Every field the assistant left that can be answered here gets an answer, as apply A7 does. */
async function answerWhatIsLeft(card: Locator) {
  const open = card.locator("li[data-left=true]").filter({
    has: page.locator("input, textarea, select, [role=group] button"),
  });
  for (;;) {
    const before = await open.count();
    if (before === 0) return;
    const row = open.first();
    const box = row.getByRole("textbox");
    const choices = row.getByRole("combobox");
    if (await box.count()) {
      const place = /location/i.test(
        (await box.getAttribute("aria-label")) ?? "",
      );
      await box.fill(place ? PLACE : TYPED);
      await row.getByRole("button", { name: "Save answer" }).click();
    } else if (await choices.count()) {
      await choices.selectOption({ index: 1 });
    } else {
      await row.getByRole("button", { name: "No", exact: true }).click();
    }
    await expect(open).toHaveCount(before - 1);
  }
}

test.beforeAll(async ({ browser }) => {
  person = invented("a12");
  context = await newContext(browser);
  page = await context.newPage();
  errors = watchErrors(page);
});

test.afterAll(async () => {
  await context.close();
  clearAttempts([person.email]);
});

test("an invented person has the consent, a base CV and two postings with their documents", async () => {
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
  postings.greenhouse = await postingOn("greenhouse");
  postings.ashby = await postingOn("ashby");
});

test("Like both postings on the jobs list: both appear in the queue", async () => {
  await page.goto("/jobs");
  for (const ats of ["greenhouse", "ashby"] as const) {
    await page
      .getByRole("checkbox", {
        name: `Select ${titles[ats]} at ${person.posting.company}`,
        exact: true,
      })
      .check();
  }
  const bar = page.getByRole("region", { name: "Bulk actions" });
  await bar.getByRole("button", { name: "Like", exact: true }).click();
  await expect(page.getByText("2 postings liked.").first()).toBeVisible();

  await page
    .getByRole("navigation", { name: "Main" })
    .getByRole("link", { name: /^Applications/ })
    .click();
  await expect(
    page.getByRole("heading", { level: 1, name: "Applications" }),
  ).toBeVisible();
  await expect(cardOf("greenhouse")).toBeVisible();
  await expect(cardOf("ashby")).toBeVisible();
});

test("the Greenhouse item: its documents, an edited answer, approved, submitted once", async () => {
  const card = cardOf("greenhouse");
  // The worker's tick starts the fill within 30 seconds; the page reads the queue by itself.
  // The Ashby form may be filled first: the two share the one browser.
  await expect(
    card.getByRole("img", { name: "The form before the click" }),
  ).toBeVisible({ timeout: 420_000 });
  const documents = card.getByRole("region", { name: "Documents" });
  await expect(documents).toContainText("CV version");
  await expect(documents).toContainText("Letter version");

  const toggle = card.getByRole("button", { name: /the answers$/ });
  if ((await toggle.getAttribute("aria-expanded")) !== "true") {
    await toggle.click();
  }
  await answerWhatIsLeft(card);
  await card.getByRole("button", { name: /^Edit: Last Name/ }).click();
  const lastName = card.getByRole("textbox", { name: /^Answer for Last Name/ });
  await lastName.fill(EDITED);
  await card.getByRole("button", { name: "Save answer" }).click();
  await expect(card.getByText(EDITED, { exact: true })).toBeVisible();
  await expect(card).toContainText("waiting for your approval");

  const approve = card.getByRole("button", { name: "Approve and submit" });
  await expect(approve).toBeEnabled();
  await approve.click();
  const dialog = page.getByRole("alertdialog");
  await expect(dialog).toContainText(
    `Submit this application to ${person.posting.company} for ${titles.greenhouse}?`,
  );
  await expect(dialog).toContainText("CV version");
  await expect(dialog).toContainText("Letter version");
  await dialog.getByRole("button", { name: "Approve and submit" }).click();

  // Under way, then submitted: a submitted application leaves the queue.
  await expect(card).toHaveCount(0, { timeout: 420_000 });
  const accepted = (await stub.submissions()).filter(
    (one) => one.ats === "greenhouse" && one.accepted,
  );
  expect(accepted).toHaveLength(1);
  expect(Object.values(accepted[0]!.fields).flat()).toContain(EDITED);
  const status = await page.request.get(`/api/jobs/${postings.greenhouse}`);
  expect(((await status.json()) as { status: string }).status).toBe("applied");
});

test("the Ashby item: rejected on a phone by keyboard, the posting skipped, nothing sent", async () => {
  await page.setViewportSize(PHONE);
  const card = cardOf("ashby");
  const reject = card.getByRole("button", { name: "Reject", exact: true });
  await expect(reject).toBeVisible({ timeout: 420_000 });
  const width = await page.evaluate(() => ({
    page: document.documentElement.scrollWidth,
    view: window.innerWidth,
  }));
  expect(width.page).toBeLessThanOrEqual(width.view);

  await reject.focus();
  await page.keyboard.press("Enter");
  const dialog = page.getByRole("alertdialog");
  await expect(dialog).toContainText(
    `Reject the application to ${person.posting.company} for ${titles.ashby}?`,
  );
  const skip = dialog.getByRole("checkbox", { name: /Skip this posting too/ });
  await skip.focus();
  await page.keyboard.press("Space");
  await expect(skip).toBeChecked();
  await dialog.getByRole("button", { name: "Reject", exact: true }).focus();
  await page.keyboard.press("Enter");

  await expect(card).toHaveCount(0);
  await expect(
    page.getByRole("heading", { level: 1, name: "Applications" }),
  ).toBeFocused();
  await expect(page.getByText(/Your queue is empty/)).toBeVisible();
  await expect(
    page
      .getByRole("navigation", { name: "Main" })
      .getByRole("link", { name: "Applications", exact: true }),
  ).toBeVisible({ timeout: 70_000 });
  const status = await page.request.get(`/api/jobs/${postings.ashby}`);
  expect(((await status.json()) as { status: string }).status).toBe("skipped");
  const sent = (await stub.submissions()).filter((one) => one.ats === "ashby");
  expect(sent).toEqual([]);
});

test("no page error was logged", () => {
  expect(errors).toEqual([]);
});
