import { expect, test, type BrowserContext, type Page } from "@playwright/test";

import { clickInView, typeInView } from "./apply-a9-helpers";
import { waitForPostingDocuments } from "./apply-document-helpers";
import { atsFormsUrl, stub } from "./apply-helpers";
import { fieldValue } from "./browser-b4-helpers";
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
 * Apply A9, Apply myself, against the real stack through Caddy plus the fake ATS forms server. An
 * invented person with the consent, a base CV and a posting whose address is the fake Ashby form
 * has its CV and letter written by the documents pipeline on the fake backend, opens the job page
 * and clicks Apply myself. The form opens in the live view with the keyboard and the mouse theirs;
 * the panel fills their identity fields and attaches the CV; the person answers the rest by
 * clicking and typing in the live view, then clicks the form's own Submit there. The watch sees
 * the site's confirmation and the person records it in one click: the posting is Applied, the CV's
 * version is frozen, the fake server holds exactly that submission, with what the person typed and
 * the CV, and the app never clicked Submit (no click and no "before" screenshot in the application's events).
 *
 * Locally, as apply-all.config.ts says (the fake forms compose file, AGENTS_FAKE_BACKEND=1 on the
 * worker), with the stack's browser up (COMPOSE_PROFILES=browser and a BROWSER_CONTROL_SECRET in
 * .env, as the local check's apply group has them), then:
 *   E2E_CADDY_URL=... E2E_ATS_FORMS_URL=... E2E_COMPOSE_PROJECT=... \
 *     pnpm --dir web exec playwright test -c e2e/apply-all.config.ts apply-a9.spec.ts
 */
test.describe.configure({ mode: "serial", timeout: 600_000 });
test.skip(!caddyUrl, "set E2E_CADDY_URL with the Compose stack running");

/** The fake forms server as the stack's browser reaches it (APPLY_STUB_BASE_URL). */
const STACK_FORMS = "http://atsforms:8080";
const KEY = `sk-ant-api03-e2egood${"Dn9x".repeat(20)}Rn42`;
const WHY = "I build robots in Nantes";

let person: Person;
let context: BrowserContext;
let page: Page;
let errors: string[];
let job: string;

type PostingDocument = {
  id: string;
  kind: string;
  latest: { number: number } | null;
};
type Version = {
  number: number;
  frozen_at: string | null;
  frozen_reason: string | null;
};

async function postingDocuments(): Promise<PostingDocument[]> {
  const response = await page.request.get(`/api/jobs/${job}/documents`);
  return ((await response.json()) as { documents: PostingDocument[] })
    .documents;
}

async function applicationOf(): Promise<{ id: string; mode: string }> {
  const listed = await page.request.get(
    `/api/applications?posting_id=${job}&limit=1`,
  );
  const [found] = (
    (await listed.json()) as { items: { id: string; mode: string }[] }
  ).items;
  if (!found) throw new Error("no application for the posting");
  return found;
}

test.beforeAll(async ({ browser }) => {
  person = invented("a9");
  context = await newContext(browser);
  page = await context.newPage();
  errors = watchErrors(page);
});

test.afterAll(async () => {
  await context.close();
  clearAttempts([person.email]);
});

test("an invented person has a posting on the fake Ashby form with its CV and letter", async () => {
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

test("the person applies in the live view, submits on the form and records it", async () => {
  await page.goto(`/jobs/${job}`);
  const section = page.getByRole("region", { name: "Apply", exact: true });
  await section.getByRole("button", { name: "Apply myself" }).click();

  const panel = page.getByTestId("manual-panel");
  await expect(
    panel.getByText(
      "This is your own session: you have the keyboard and the mouse.",
    ),
  ).toBeVisible({ timeout: 120_000 });
  await expect(panel.getByRole("button", { name: /Take over/ })).toHaveCount(0);

  // The panel: the identity fields by the app, then the CV on the form's Resume input.
  await panel.getByRole("button", { name: "Fill my details" }).click();
  await expect(
    panel.getByText(/Filled \d+ fields? with your details/),
  ).toBeVisible({ timeout: 120_000 });
  expect(fieldValue("#_systemfield_name")).toBe("Morgan Exampleton");
  await panel.getByRole("button", { name: "Attach my CV" }).click();
  await expect(panel.getByText("Your CV is attached to the form.")).toBeVisible(
    { timeout: 60_000 },
  );
  // A watch after the attach (its idle seconds changed) reports no file chooser waiting.
  const manualOf = async () => {
    const id = (await applicationOf()).id;
    const read = await page.request.get(`/api/applications/${id}/manual`);
    return (await read.json()) as {
      chooser: boolean;
      idle_left_s: number | null;
    };
  };
  const before = (await manualOf()).idle_left_s;
  await expect
    .poll(async () => (await manualOf()).idle_left_s, { timeout: 30_000 })
    .not.toBe(before);
  expect((await manualOf()).chooser).toBe(false);
  await expect(panel.getByText("The form asks for a file")).toHaveCount(0);

  // The person, through the live view only: the location from the list, two answers, a typed
  // reason, the consent, then the form's own Submit.
  await clickInView(page, "#_systemfield_location");
  await typeInView(page, "Paris");
  await expect
    .poll(() => fieldValue("#_systemfield_location"), { timeout: 15_000 })
    .toBe("Paris");
  await clickInView(
    page,
    '[data-field="_systemfield_location"] li[role=option]',
  );
  await expect
    .poll(() => fieldValue("#_systemfield_location"), { timeout: 15_000 })
    .toBe("Paris, France");
  await clickInView(page, '[data-field="q_eu_work"] button[data-option="yes"]');
  await clickInView(page, '[data-field="q_sponsor"] button[data-option="no"]');
  await clickInView(page, "#q_why");
  await typeInView(page, WHY);
  await expect.poll(() => fieldValue("#q_why"), { timeout: 15_000 }).toBe(WHY);
  await clickInView(page, "#consent");
  // The fake Ashby flags a submit that comes under 3 s after the last input as spam, as the real
  // one does: the person clicks Submit again until the site takes it. Each click posts once and the
  // fake records every post, taken or flagged: the next click waits for the last one's record, or
  // it may come while the taken post is still on its way and find the confirmation page instead
  // of the button (it did under load).
  const posts = async () =>
    (await stub.submissions()).filter((one) => one.ats === "ashby");
  const accepted = async () => (await posts()).filter((one) => one.accepted);
  await expect
    .poll(
      async () => {
        const before = await posts();
        if (before.some((one) => one.accepted)) return 1;
        await clickInView(page, "button[data-submit]");
        await expect
          .poll(async () => (await posts()).length, { timeout: 20_000 })
          .toBeGreaterThan(before.length);
        return (await accepted()).length;
      },
      { timeout: 60_000 },
    )
    .toBe(1);

  // The watch sees the site's confirmation: one click records it.
  await panel
    .getByRole("button", { name: "Record as applied" })
    .click({ timeout: 60_000 });
  await expect(section).toContainText("Applied.", { timeout: 30_000 });
  await expect(section).toContainText("You sent this form yourself.");

  const sent = await accepted();
  expect(sent).toHaveLength(1);
  for (const one of await stub.submissions())
    expect(one.accepted || one.reason === "invisible_check").toBe(true);
  expect(sent[0]?.fields.q_why).toBe(WHY);
  expect(sent[0]?.files.map((one) => one.field)).toContain(
    "_systemfield_resume",
  );

  const posting = await page.request.get(`/api/jobs/${job}`);
  expect(((await posting.json()) as { status: string }).status).toBe("applied");
  const cv = (await postingDocuments()).find((one) => one.kind === "cv");
  const versions = await page.request.get(`/api/documents/${cv?.id}/versions`);
  const latest = ((await versions.json()) as Version[]).sort(
    (a, b) => b.number - a.number,
  )[0];
  expect(latest?.frozen_reason).toBe("apply");
  expect(latest?.frozen_at).not.toBeNull();

  // The app never clicked Submit: no click event, the person's record with the confirmation seen.
  const application = await applicationOf();
  expect(application.mode).toBe("manual");
  const detail = await page.request.get(`/api/applications/${application.id}`);
  const events = (
    (await detail.json()) as {
      events: { kind: string; data: Record<string, unknown> }[];
    }
  ).events;
  const kinds = events.map((one) => one.kind);
  expect(kinds).not.toContain("click");
  expect(kinds).not.toContain("before");
  expect(events.find((one) => one.kind === "recorded")?.data.by).toBe(
    "confirmation",
  );
});

test("no page error was logged", () => {
  expect(errors).toEqual([]);
});
