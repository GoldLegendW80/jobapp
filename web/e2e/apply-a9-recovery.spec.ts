import { expect, test, type BrowserContext, type Page } from "@playwright/test";

import {
  clickAtInView,
  closeRemoteWindow,
  remoteSpot,
} from "./apply-a9-helpers";
import { stub } from "./apply-helpers";
import {
  CSRF,
  addPosting,
  caddyUrl,
  invented,
  signUp,
  waitForRender,
  type Person,
} from "./documents-helpers";
import { clearAttempts, makeShots, newContext, watchErrors } from "./stack";

test.describe.configure({ mode: "serial", timeout: 600_000 });
test.skip(!caddyUrl, "set E2E_CADDY_URL with the Compose stack running");
const shots = makeShots("apply-recovery-runtime", "manual-recovery");
const KEY = `sk-ant-api03-e2egood${"Qa10x".repeat(20)}Rn42`;
let person: Person;
let context: BrowserContext;
let page: Page;
let errors: string[];
let otherContext: BrowserContext | undefined;
let otherPerson: Person | undefined;
type Application = { id: string; state: string; needs: string | null };
async function application(job: string): Promise<Application | undefined> {
  const response = await page.request.get(
    `/api/applications?posting_id=${job}&limit=1`,
  );
  return ((await response.json()) as { items: Application[] }).items[0];
}
async function manual(
  id: string,
): Promise<{ confirmation_seen: boolean; idle_left_s: number; phase: string }> {
  const response = await page.request.get(`/api/applications/${id}/manual`);
  expect(response.status()).toBe(200);
  return (await response.json()) as {
    confirmation_seen: boolean;
    idle_left_s: number;
    phase: string;
  };
}
async function posting(reason: string): Promise<string> {
  const job = await addPosting(page.request, {
    ...person,
    posting: { ...person.posting, title: `${person.posting.title} ${reason}` },
  });
  const read = await page.request.get(`/api/jobs/${job}`);
  const { updated_at } = (await read.json()) as { updated_at: string };
  expect(
    (
      await page.request.patch(`/api/jobs/${job}`, {
        headers: CSRF,
        data: {
          url: `http://atsforms:8080/forms/a9/recovery.html?job=${reason}`,
          fit: 90,
          updated_at,
        },
      })
    ).status(),
  ).toBe(200);
  expect(
    (
      await page.request.post(`/api/jobs/${job}/documents/write`, {
        headers: CSRF,
        data: { kinds: ["cv", "letter"], script: "journey" },
      })
    ).status(),
  ).toBe(202);
  await expect
    .poll(
      async () => {
        const read = await page.request.get(`/api/jobs/${job}/documents`);
        const { documents } = (await read.json()) as {
          documents: { latest: unknown }[];
        };
        return (
          documents.length === 2 &&
          documents.every((one) => one.latest !== null)
        );
      },
      { timeout: 150_000 },
    )
    .toBe(true);
  return job;
}

test.beforeAll(async ({ browser }) => {
  person = invented("a9-recovery");
  context = await newContext(browser);
  page = await context.newPage();
  errors = watchErrors(page);
});
test.afterAll(async () => {
  const read = await page.request.get("/api/applications?limit=50");
  const { items } = (await read.json()) as { items: Application[] };
  for (const app of items) {
    if (["queued", "filling", "needs_person", "ready"].includes(app.state))
      await page.request.post(`/api/applications/${app.id}/manual/close`, {
        headers: CSRF,
      });
  }
  await otherContext?.close();
  await context.close();
  clearAttempts([person.email, ...(otherPerson ? [otherPerson.email] : [])]);
});
test("an invented candidate prepares documents", async () => {
  await stub.reset();
  await signUp(page, person);
  expect(
    (
      await page.request.put("/api/me/model-credential", {
        headers: CSRF,
        data: { kind: "anthropic_api_key", secret: KEY },
      })
    ).status(),
  ).toBe(200);
  const read = await page.request.get("/api/documents/consent");
  const { version } = (await read.json()) as { version: number };
  expect(
    (
      await page.request.post("/api/documents/consent", {
        headers: CSRF,
        data: { version },
      })
    ).status(),
  ).toBe(200);
  const built = await page.request.post("/api/documents/base-cvs", {
    headers: CSRF,
    data: { from: "profile", script: "documents_base_journey" },
  });
  expect(built.status()).toBe(202);
  const { base_cv } = (await built.json()) as { base_cv: { id: string } };
  await waitForRender(page.request, base_cv.id, 1);
});

async function open(job: string): Promise<string> {
  await page.goto(`/jobs/${job}`);
  await page
    .getByRole("region", { name: "Apply", exact: true })
    .getByRole("button", { name: "Apply myself", exact: true })
    .click();
  const panel = page.getByTestId("manual-panel");
  await expect(
    panel.getByText(
      "This is your own session: you have the keyboard and the mouse.",
    ),
  ).toBeVisible({ timeout: 120_000 });
  return (await application(job))!.id;
}
async function events(
  id: string,
): Promise<{ kind: string; data: Record<string, unknown> }[]> {
  const read = await page.request.get(`/api/applications/${id}`);
  return (
    (await read.json()) as {
      events: { kind: string; data: Record<string, unknown> }[];
    }
  ).events;
}

test("immediate Close after person Submit performs the final watch and freezes both documents once", async () => {
  const job = await posting("final-close");
  const id = await open(job);
  const panel = page.getByTestId("manual-panel");
  await panel
    .getByRole("button", { name: "Fill my details", exact: true })
    .click();
  await expect(
    panel.getByText(/Filled \d+ fields? with your details/),
  ).toBeVisible({ timeout: 60_000 });
  for (const [button, done] of [
    ["Attach my CV", "Your CV is attached to the form."],
    ["Attach my letter", "Your letter is attached to the form."],
  ] as const) {
    await panel.getByRole("button", { name: button, exact: true }).click();
    await expect(panel.getByText(done, { exact: true })).toBeVisible({
      timeout: 60_000,
    });
  }
  expect((await manual(id)).confirmation_seen).toBe(false);
  const spot = remoteSpot("#submit");
  const idle = (await manual(id)).idle_left_s;
  await expect
    .poll(async () => (await manual(id)).idle_left_s, {
      timeout: 15_000,
      intervals: [100],
    })
    .not.toBe(idle);
  expect((await manual(id)).confirmation_seen).toBe(false);
  await clickAtInView(page, spot);
  await expect
    .poll(
      async () =>
        (await stub.submissions()).filter((one) => one.org === "a9-recovery")
          .length,
      { timeout: 5_000, intervals: [100] },
    )
    .toBe(1);
  const closed = await page.request.post(
    `/api/applications/${id}/manual/close`,
    { headers: CSRF },
  );
  expect(closed.status()).toBe(200);
  await expect
    .poll(async () => (await application(job))?.state, { timeout: 30_000 })
    .toBe("submitted");
  const recorded = await events(id);
  expect(
    recorded.filter((one) => one.kind === "manual_close_requested"),
  ).toHaveLength(1);
  expect(recorded.filter((one) => one.kind === "recorded")).toEqual([
    expect.objectContaining({
      data: expect.objectContaining({ by: "confirmation" }),
    }),
  ]);
  expect(recorded.some((one) => one.kind === "click")).toBe(false);
  expect(
    (await stub.submissions()).filter((one) => one.org === "a9-recovery"),
  ).toHaveLength(1);
  const docs = await page.request.get(`/api/jobs/${job}/documents`);
  for (const doc of ((await docs.json()) as { documents: { id: string }[] })
    .documents) {
    const read = await page.request.get(`/api/documents/${doc.id}/versions`);
    const versions = (await read.json()) as { frozen_reason: string | null }[];
    expect(
      versions.filter((one) => one.frozen_reason === "apply"),
    ).toHaveLength(1);
  }
  await shots(page, "manual-final-close");
});

test("an uncertain ended session stays held and visible, can reopen, and records only on the person's decision", async ({
  browser,
}) => {
  const before = (await stub.submissions()).length;
  const job = await posting("unknown");
  const id = await open(job);
  otherPerson = invented("a9-other");
  otherContext = await newContext(browser);
  const other = await otherContext.newPage();
  await signUp(other, otherPerson);
  for (const path of [
    `/api/applications/${id}`,
    `/api/applications/${id}/manual`,
  ])
    expect((await other.request.get(path)).status()).toBe(404);
  expect(
    (
      await other.request.post(`/api/applications/${id}/manual/close`, {
        headers: CSRF,
      })
    ).status(),
  ).toBe(404);
  expect(
    (
      await other.request.post("/api/browser/live/ticket", {
        headers: CSRF,
        data: { viewer: "8e2b21c8-1abb-4edc-bd53-720d930f3e6c" },
      })
    ).status(),
  ).toBe(403);
  closeRemoteWindow();
  await expect
    .poll(
      async () => {
        const app = await application(job);
        return [app?.state, app?.needs];
      },
      { timeout: 30_000 },
    )
    .toEqual(["queued", "session_ended"]);
  await page.goto("/applications");
  const card = page
    .getByRole("article")
    .filter({ hasText: `${person.posting.title} unknown` });
  await expect(card).toBeVisible({ timeout: 30_000 });
  await expect(
    card.getByRole("button", { name: "Cancel", exact: true }),
  ).toHaveCount(0);
  await shots(page, "manual-unknown-queue");
  await page.goto(`/jobs/${job}`);
  await page
    .getByRole("button", { name: "Open my browser again", exact: true })
    .click();
  await expect
    .poll(async () => (await manual(id)).phase, { timeout: 120_000 })
    .toBe("open");
  expect((await application(job))?.id).toBe(id);
  closeRemoteWindow();
  await expect
    .poll(async () => (await application(job))?.needs, { timeout: 30_000 })
    .toBe("session_ended");
  await page.reload();
  await page
    .getByRole("button", { name: "I submitted it", exact: true })
    .click();
  await page
    .getByRole("alertdialog")
    .getByRole("button", { name: "Record as sent", exact: true })
    .click();
  await expect
    .poll(async () => (await application(job))?.state, { timeout: 30_000 })
    .toBe("submitted");
  expect((await stub.submissions()).length).toBe(before);
  const recorded = await events(id);
  expect(recorded.filter((one) => one.kind === "recorded")).toEqual([
    expect.objectContaining({
      data: expect.objectContaining({ by: "person" }),
    }),
  ]);
  expect(recorded.some((one) => one.kind === "click")).toBe(false);
  await shots(page, "manual-person-recorded");
});
test("no page error was logged", () => {
  expect(errors).toEqual([]);
});
