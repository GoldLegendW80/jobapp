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
 * Apply A11 against the real stack through Caddy plus the fake ATS forms server: a company cap
 * set on the Applications settings page holds back a second application to that company. An
 * invented person opens Settings from the account menu, adds "Example Robotics: at most 1
 * application in 90 days" with the caps editor, applies once on the fake Example Robotics form
 * (Ashby) and sees Applied, then has a second posting filled on another job address of the same
 * company (the fake server's slow variant, a job id of its own): its approval is refused with the
 * cap sentence, the application keeps waiting for the person, and the fake server holds one
 * submission. Both postings name another company: the cap matches them through the ATS
 * organisation of their form, as caps.company_keys reads it.
 *
 * Run as apply-a7.spec.ts says, on the same stack:
 *   E2E_CADDY_URL=... E2E_ATS_FORMS_URL=... E2E_COMPOSE_PROJECT=... \
 *     pnpm --dir web exec playwright test -c e2e/apply-all.config.ts apply-a11.spec.ts
 */
// Two fills (about 100 s each, the slow variant 10 s more per page) after the documents are
// written, and one submit lease (about 120 s): the budget is the sum of the waits below.
test.describe.configure({ mode: "serial", timeout: 600_000 });
test.skip(!caddyUrl, "set E2E_CADDY_URL with the Compose stack running");

const TYPED = "Not applicable";
const PLACE = "Paris, France"; // one of the places of the fake Ashby location typeahead
/** The fake forms server as the stack's browser reaches it (APPLY_STUB_BASE_URL). */
const STACK_FORMS = "http://atsforms:8080";
const KEY = `sk-ant-api03-e2egood${"Dn9x".repeat(20)}Rn42`;
const COMPANY = "Example Robotics"; // the company of the fake Ashby form (tests/ats_forms_stub.py)
const CAP_SENTENCE =
  "You set a limit of applications for this company and it is reached. You can change it in Settings, Applications.";

let person: Person;
let context: BrowserContext;
let page: Page;
let errors: string[];

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

/** A posting of the person on the fake Ashby form `variant`, with its two documents. */
async function postingOn(variant: "" | "slow", role: string): Promise<string> {
  const job = await addPosting(page.request, {
    ...person,
    posting: { ...person.posting, title: person.posting.title + role },
  });
  const posting = (await stub.posting("ashby", variant)).replace(
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
  await expect.poll(() => documentsReady(job), { timeout: 150_000 }).toBe(true);
  return job;
}

/** Apply (Review first), wait for the fill, answer what it left; the Apply section. */
async function fillForApproval(job: string) {
  await page.goto(`/jobs/${job}`);
  const section = page.getByRole("region", { name: "Apply" });
  await section.getByRole("button", { name: "Apply (Review first)" }).click();
  await expect(
    section.getByRole("img", { name: /before the click/ }),
  ).toBeVisible({ timeout: 240_000 });
  const open = section.locator("li[data-left=true]").filter({
    has: page.locator("input, textarea, select, [role=group] button"),
  });
  for (;;) {
    const before = await open.count();
    if (before === 0) break;
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
  await expect(section).toContainText("waiting for your approval");
  return section;
}

async function approve(section: Locator) {
  await section.getByRole("button", { name: "Approve and submit" }).click();
  const dialog = page.getByRole("alertdialog");
  await expect(dialog).toContainText("Submit this application to");
  await dialog.getByRole("button", { name: "Approve and submit" }).click();
}

const acceptedOnAshby = async () =>
  (await stub.submissions()).filter(
    (one) => one.ats === "ashby" && one.accepted,
  );

test.beforeAll(async ({ browser }) => {
  person = invented("a11");
  context = await newContext(browser);
  page = await context.newPage();
  // The refused approval is a 409 the page answers with the cap sentence.
  errors = watchErrors(page, [[409, /\/api\/applications\/[^/]+\/approve/]]);
});

test.afterAll(async () => {
  await context.close();
  clearAttempts([person.email]);
});

test("an invented person has the consent and a base CV", async () => {
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
});

test("the person adds a cap of 1 for the company on the Applications page", async () => {
  await page.goto("/account");
  await page.getByRole("button", { name: /Account menu/ }).click();
  await page.getByRole("menuitem", { name: "Settings" }).click();
  await expect(page).toHaveURL(/\/settings\/applications$/);
  await expect(
    page.getByRole("heading", { name: "Application settings", level: 1 }),
  ).toBeVisible();
  await expect(
    page.getByRole("radio", { name: "Full auto" }),
  ).not.toBeChecked();

  await page.getByRole("button", { name: "Add a cap" }).click();
  const form = page.getByRole("form", { name: "New cap" });
  await expect(form.getByLabel("Company")).toBeFocused();
  await page.keyboard.type(COMPANY);
  await form.getByLabel("At most this many applications").fill("1");
  await form.getByLabel("In this many days").fill("90");
  await form.getByLabel("In this many days").press("Enter");
  await expect(page.getByText("Cap added.")).toBeVisible();
  const caps = page.getByRole("list", { name: "Your caps" });
  await expect(caps).toContainText(
    `${COMPANY}: at most 1 application in 90 days`,
  );
  await expect(caps).toContainText("0 of 1 used in the last 90 days");

  await page.setViewportSize({ width: 375, height: 812 });
  try {
    const fits = await page.evaluate(
      () =>
        document.documentElement.scrollWidth <=
        document.documentElement.clientWidth,
    );
    expect(fits, "the Applications page: no sideways scroll at 375px").toBe(
      true,
    );
  } finally {
    await page.setViewportSize({ width: 1280, height: 800 });
  }
});

test("the first application to the company is approved and submitted", async () => {
  const job = await postingOn("", "");
  const section = await fillForApproval(job);
  await approve(section);
  await expect(section).toContainText("Applied.", { timeout: 180_000 });
  expect(await acceptedOnAshby()).toHaveLength(1);

  await page.goto("/settings/applications");
  await expect(page.getByRole("list", { name: "Your caps" })).toContainText(
    "1 of 1 used in the last 90 days",
  );
});

test("the approval of a second posting of the company is refused with the cap sentence", async () => {
  const job = await postingOn("slow", ", Developer Tools");
  const section = await fillForApproval(job);
  await approve(section);
  await expect(section).toContainText(CAP_SENTENCE);
  // The cap leaves the form ready: it waits for the person, and nothing was sent.
  await expect(section).toContainText("waiting for your approval");
  await expect(
    section.getByRole("button", { name: "Approve and submit" }),
  ).toBeVisible();
  expect(await acceptedOnAshby()).toHaveLength(1);
});

test("no page error was logged", () => {
  expect(errors).toEqual([]);
});
