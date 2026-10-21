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
 * Apply A13b against the real stack through Caddy plus the fake ATS forms server: Full auto. An
 * invented person with the consent, a base CV, a company cap that lets three applications go to
 * the one company of the invented postings (a cap of the person replaces Full auto's one per
 * company rule), and three postings on the plain fake Greenhouse forms (nothing a profile
 * leaves, no free text, so no check by model is needed), each with its CV and letter written by
 * the documents pipeline on the fake backend.
 *
 * The person turns Full auto on in Settings, Applications (what a Like then does is said first)
 * and sets the hold to its floor, which this stack lowers to 60 s through its own settings
 * (tests/ats-forms-stub.compose.yml: APPLY_HOLD_S 60, APPLY_REHEARSALS 1). The first Like is the
 * rehearsal: filled, checked, nothing sent, then "Does this look right?" in the queue; Yes keeps
 * it there ready, and one click Approve submits it. Then two Likes: each is held with a
 * countdown; one is cancelled during its hold on a phone by keyboard and never reaches the
 * fake, the other is submitted by the app after its hold. The fake holds exactly two accepted
 * submissions, the approved rehearsal and the Full auto one.
 *
 * Locally, as apply-all.config.ts says (the fake forms compose file, AGENTS_FAKE_BACKEND=1 on
 * the worker, the stack's browser up with COMPOSE_PROFILES=browser), then:
 *   E2E_CADDY_URL=... E2E_ATS_FORMS_URL=... E2E_COMPOSE_PROJECT=... \
 *     pnpm --dir web exec playwright test -c e2e/apply-all.config.ts apply-a13.spec.ts
 */
// Three fills of about 100 s each and three submit leases of about 120 s each (the rehearsal's
// stops before the click), one after the other on the one browser, plus a hold of 60 s.
test.describe.configure({ mode: "serial", timeout: 900_000 });
test.skip(!caddyUrl, "set E2E_CADDY_URL with the Compose stack running");

/** The fake forms server as the stack's browser reaches it (tests/ats-forms-stub.compose.yml). */
const STACK_FORMS = "http://atsforms:8080";
const KEY = `sk-ant-api03-e2egood${"Fa13".repeat(20)}Rn42`;
const PHONE = { width: 390, height: 844 };
const FILL_MS = 420_000;

let person: Person;
let context: BrowserContext;
let page: Page;
let errors: string[];
type Key = "rehearsal" | "submitted" | "cancelled";
/** The fake Greenhouse form of each posting (one job each), and what its title adds to the role. */
const FORMS: Record<Key, { job: string; suffix: string }> = {
  rehearsal: { job: "4012345", suffix: "" },
  submitted: { job: "4012346", suffix: ", Fleet" },
  cancelled: { job: "4012347", suffix: ", Controls" },
};
const KEYS = Object.keys(FORMS) as Key[];
const postings: Record<Key, string> = {
  rehearsal: "",
  submitted: "",
  cancelled: "",
};
const titles: Record<Key, string> = {
  rehearsal: "",
  submitted: "",
  cancelled: "",
};

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

async function postingOn(key: Key): Promise<string> {
  const title = person.posting.title + FORMS[key].suffix;
  titles[key] = title;
  const job = await addPosting(page.request, {
    ...person,
    posting: { ...person.posting, title },
  });
  const posting = (
    await stub.posting("greenhouse", "plain", FORMS[key].job)
  ).replace(atsFormsUrl ?? "", STACK_FORMS);
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

const cardOf = (key: Key): Locator =>
  page.getByRole("article", { name: titles[key], exact: true });

async function like(keys: Key[]) {
  await page.goto("/jobs");
  for (const key of keys) {
    await page
      .getByRole("checkbox", {
        name: `Select ${titles[key]} at ${person.posting.company}`,
        exact: true,
      })
      .check();
  }
  const bar = page.getByRole("region", { name: "Bulk actions" });
  await bar.getByRole("button", { name: "Like", exact: true }).click();
  await expect(
    page
      .getByText(
        keys.length === 1
          ? "1 posting liked."
          : `${keys.length} postings liked.`,
      )
      .first(),
  ).toBeVisible();
  await page
    .getByRole("navigation", { name: "Main" })
    .getByRole("link", { name: /^Applications/ })
    .click();
  await expect(
    page.getByRole("heading", { level: 1, name: "Application queue" }),
  ).toBeVisible();
}

const accepted = async () =>
  (await stub.submissions()).filter(
    (one) => one.ats === "greenhouse" && one.accepted,
  );

test.beforeAll(async ({ browser }) => {
  person = invented("a13");
  context = await newContext(browser);
  page = await context.newPage();
  errors = watchErrors(page);
});

test.afterAll(async () => {
  await context.close();
  clearAttempts([person.email]);
});

test("an invented person has the consent, a base CV, a company cap and three postings", async () => {
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
  const caps = await page.request.get("/api/apply/caps");
  const { revision } = (await caps.json()) as { revision: string };
  const capped = await page.request.put("/api/apply/caps", {
    headers: CSRF,
    data: {
      items: [
        {
          label: person.posting.company,
          max_count: 3,
          window_days: 30,
          same_role_days: null,
        },
      ],
      revision,
    },
  });
  expect(capped.status()).toBe(200);
  const built = await page.request.post("/api/documents/base-cvs", {
    headers: CSRF,
    data: { from: "profile", script: "documents_base_journey" },
  });
  expect(built.status()).toBe(202);
  const base = ((await built.json()) as { base_cv: { id: string } }).base_cv;
  await waitForRender(page.request, base.id, 1);
  for (const key of KEYS) postings[key] = await postingOn(key);
});

test("Full auto is turned on in Settings after it says what a Like then does, and the hold set to its floor", async () => {
  const before = await page.request.get("/api/apply/settings");
  const { min_fit } = (await before.json()) as { min_fit: number };
  await page.goto("/settings/applications");
  const review = page.getByRole("radio", { name: "Review first" });
  await expect(review).toBeChecked();
  const full = page.getByRole("radio", { name: "Full auto" });
  await expect(full).toBeEnabled();
  await review.focus();
  await page.keyboard.press("ArrowDown");
  await expect(full).toBeChecked();
  const panel = page.getByRole("region", { name: "Turn on Full auto" });
  await expect(panel).toContainText(
    `The app will submit applications for you when a posting's fit is at least ${min_fit}, at least 1 minute after each Like unless you cancel, and never more than one application per company in 30 days.`,
  );
  await expect(panel).toContainText(
    'Your next Like is a rehearsal: the app fills the form and checks it, submits nothing, and asks you "Does this look right?" in Applications.',
  );
  await panel.getByRole("button", { name: "Turn on Full auto" }).click();
  await expect(page.getByText("Full auto is on.").first()).toBeVisible();
  await expect(
    page.getByRole("region", { name: "Full auto is on" }),
  ).toBeVisible();

  await page.getByText("Advanced", { exact: true }).click();
  await expect(page.getByText(/Never less than 1 minute/)).toBeVisible();
  // The stack's floor is the hold already: Save waits for a change, then takes 2 and back 1.
  const hold = page.getByRole("textbox", { name: "Hold length in minutes" });
  const save = page.getByRole("button", { name: "Save hold length" });
  await expect(hold).toHaveValue("1");
  await expect(save).toBeDisabled();
  for (const [minutes, said] of [
    ["2", "2 minutes between a Full auto"],
    ["1", "1 minute between a Full auto"],
  ] as const) {
    await hold.fill(minutes);
    await save.click();
    await expect(page.getByText(said)).toBeVisible();
  }
  const settings = await page.request.get("/api/apply/settings");
  expect(await settings.json()).toMatchObject({
    mode_after_like: "full_auto",
    hold_s: 60,
    rehearsals: 1,
    rehearsals_done: 0,
  });
});

test("the first Like is a rehearsal: nothing sent, Yes keeps it ready, one click Approve submits it", async () => {
  await like(["rehearsal"]);
  const card = cardOf("rehearsal");
  const question = card.getByRole("region", { name: "Does this look right?" });
  await expect(question).toBeVisible({ timeout: FILL_MS });
  await expect(card).toContainText("Nothing was sent.");
  await expect(
    card.getByRole("img", { name: "The form before the click" }),
  ).toBeVisible();
  expect(await accepted()).toEqual([]);

  await question.getByRole("button", { name: "Yes, it looks right" }).click();
  await expect(
    page
      .getByText("Thank you. It waits in your queue, ready for your approval.")
      .first(),
  ).toBeVisible();
  await expect(card).toContainText("waiting for your approval");
  const approve = card.getByRole("button", { name: "Approve and submit" });
  await expect(approve).toBeEnabled();
  await approve.click();
  const dialog = page.getByRole("alertdialog");
  await expect(dialog).toContainText(
    `Submit this application to ${person.posting.company} for ${titles.rehearsal}?`,
  );
  await dialog.getByRole("button", { name: "Approve and submit" }).click();
  await expect(dialog).toBeHidden();
  await expect(card).toHaveCount(0, { timeout: FILL_MS });
  const sent = await accepted();
  expect(sent.map((one) => [one.job, one.variant])).toEqual([
    [FORMS.rehearsal.job, "plain"],
  ]);
  const status = await page.request.get(`/api/jobs/${postings.rehearsal}`);
  expect(((await status.json()) as { status: string }).status).toBe("applied");
});

test("after the rehearsal, a Like held then cancelled on a phone by keyboard never reaches the fake", async () => {
  await like(["submitted", "cancelled"]);
  await page.setViewportSize(PHONE);
  const card = cardOf("cancelled");
  const timer = card.getByRole("timer");
  await expect(timer).toBeVisible({ timeout: FILL_MS });
  await expect(card).toContainText("unless you cancel");
  const width = await page.evaluate(() => ({
    page: document.documentElement.scrollWidth,
    view: window.innerWidth,
  }));
  expect(width.page).toBeLessThanOrEqual(width.view);

  await card.getByRole("button", { name: "Cancel application" }).focus();
  await page.keyboard.press("Enter");
  const dialog = page.getByRole("alertdialog");
  await expect(dialog).toContainText("The hold stops and nothing is sent.");
  await dialog.getByRole("button", { name: "Cancel the application" }).focus();
  await page.keyboard.press("Enter");
  await expect(
    page.getByText("Cancelled during the hold. Nothing was sent.").first(),
  ).toBeVisible();
  await expect(card).toHaveCount(0);
  const read = await page.request.get(
    `/api/applications?posting_id=${postings.cancelled}&limit=1`,
  );
  const { items } = (await read.json()) as {
    items: Array<{ state: string; mode: string }>;
  };
  expect(items[0]).toMatchObject({ state: "cancelled", mode: "full_auto" });
  await page.setViewportSize({ width: 1280, height: 800 });
});

test("the other Like is held, then submitted once by the app after its hold", async () => {
  const card = cardOf("submitted");
  // Held with its countdown, or already past it: either way the app submits it by itself.
  await expect(card).toHaveCount(0, { timeout: FILL_MS });
  const sent = await accepted();
  expect(sent.map((one) => one.job).sort()).toEqual(
    [FORMS.rehearsal.job, FORMS.submitted.job].sort(),
  );
  expect(sent.filter((one) => one.job === FORMS.cancelled.job)).toEqual([]);
  const status = await page.request.get(`/api/jobs/${postings.submitted}`);
  expect(((await status.json()) as { status: string }).status).toBe("applied");
  const read = await page.request.get(
    `/api/applications?posting_id=${postings.submitted}&limit=1`,
  );
  const { items } = (await read.json()) as {
    items: Array<{ id: string; state: string; mode: string }>;
  };
  expect(items[0]).toMatchObject({ state: "submitted", mode: "full_auto" });
  // Held first: the click came after the end of the hold the decision wrote.
  const detail = await page.request.get(`/api/applications/${items[0]!.id}`);
  const { events } = (await detail.json()) as {
    events: Array<{ at: string; kind: string; data: Record<string, unknown> }>;
  };
  const held = events.find(
    (one) => one.kind === "decision" && one.data.outcome === "held",
  );
  const click = events.find((one) => one.kind === "click");
  expect(held && click).toBeTruthy();
  expect(Date.parse(click!.at)).toBeGreaterThanOrEqual(
    Date.parse(String(held!.data.until)),
  );
  expect(
    Date.parse(String(held!.data.until)) - Date.parse(held!.at),
  ).toBeGreaterThanOrEqual(60_000);

  const today = page.getByRole("region", { name: "Full auto today" });
  await expect(today).toContainText("1 submitted", { timeout: 60_000 });
});

test("no page error was logged", () => {
  expect(errors).toEqual([]);
});
