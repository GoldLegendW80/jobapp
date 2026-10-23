import { expect, test, type BrowserContext, type Page } from "@playwright/test";

import { clickInView, typeInView } from "./apply-a9-helpers";
import { waitForPostingDocuments } from "./apply-document-helpers";
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
const shots = makeShots("apply-recovery-runtime", "person-wait");
const KEY = `sk-ant-api03-e2egood${"Qa10x".repeat(20)}Rn42`;
let person: Person;
let context: BrowserContext;
let page: Page;
let errors: string[];
type Application = { id: string; state: string; needs: string | null };
type Detail = {
  answers: { field_key: string; label: string; value: string | null }[];
  events: { kind: string; data: Record<string, unknown> }[];
};
type Lease = {
  id: string;
  state: string;
  wait_reason: string | null;
  active_s: number;
  wait_deadline: string | null;
};

async function application(job: string): Promise<Application | undefined> {
  const response = await page.request.get(
    `/api/applications?posting_id=${job}&limit=1`,
  );
  return ((await response.json()) as { items: Application[] }).items[0];
}
async function lease(): Promise<Lease | null> {
  const response = await page.request.get("/api/browser");
  return ((await response.json()) as { lease: Lease | null }).lease;
}
async function detail(id: string): Promise<Detail> {
  const response = await page.request.get(`/api/applications/${id}`);
  expect(response.status()).toBe(200);
  const events = ((await response.json()) as Detail).events;
  const approval = await page.request.get(`/api/applications/${id}/approval`);
  expect(approval.status()).toBe(200);
  return { ...((await approval.json()) as Detail), events };
}
async function posting(reason: string): Promise<string> {
  const number = reason === "captcha" ? "1011" : "1012";
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
          url: `http://atsforms:8080/forms/a15/workday.html?job=${number}&variant=${reason}`,
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
  await waitForPostingDocuments(page.request, job);
  return job;
}

test.beforeAll(async ({ browser }) => {
  person = invented("a10a");
  context = await newContext(browser);
  page = await context.newPage();
  errors = watchErrors(page);
});
test.afterAll(async () => {
  const read = await page.request.get("/api/applications?limit=50");
  const { items } = (await read.json()) as { items: Application[] };
  for (const app of items) {
    if (
      [
        "queued",
        "filling",
        "needs_person",
        "ready",
        "approved",
        "held",
      ].includes(app.state)
    )
      await page.request.post(`/api/applications/${app.id}/cancel`, {
        headers: CSRF,
      });
  }
  await context.close();
  clearAttempts([person.email]);
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

for (const reason of ["captcha", "email_code"]) {
  test(`later Workday ${reason} preserves earlier answers and submits once after a fresh approval`, async () => {
    const before = (await stub.submissions()).length;
    const job = await posting(reason);
    await page.goto(`/jobs/${job}`);
    const section = page.getByRole("region", { name: "Apply", exact: true });
    await section
      .getByRole("button", { name: "Apply (Review first)", exact: true })
      .click();
    await expect
      .poll(async () => (await application(job))?.needs, { timeout: 180_000 })
      .toBe(reason);
    await expect
      .poll(async () => (await lease())?.state, { timeout: 30_000 })
      .toBe("waiting_person");
    const waiting = await lease();
    expect(waiting?.wait_reason).toBe(reason);
    expect(waiting?.active_s).toBeLessThan(900);
    await expect(
      page.getByRole("region", { name: "Sign in to atsforms", exact: true }),
    ).toHaveCount(0);
    await expect(
      page.getByRole("button", { name: "I'm signed in", exact: true }),
    ).toHaveCount(0);
    await expect(page.getByTestId("live-screen").locator("canvas")).toBeVisible(
      { timeout: 60_000 },
    );
    await expect(
      page.getByText(
        reason === "captcha"
          ? "A captcha needs you"
          : "An e-mail code needs you",
        { exact: true },
      ),
    ).toBeVisible();
    expect((await stub.submissions()).length).toBe(before);
    await expect(
      page.getByRole("button", { name: "Take over", exact: true }),
    ).toHaveCount(0);
    await expect(
      page.getByRole("button", { name: "Keyboard", exact: true }),
    ).toBeVisible();
    await expect
      .poll(
        async () =>
          page.getByTestId("live-screen").evaluate((screen) => {
            const canvas = screen.querySelector("canvas");
            const context = canvas?.getContext("2d");
            if (!canvas || !context || canvas.width === 0) return false;
            const { data } = context.getImageData(
              0,
              0,
              canvas.width,
              canvas.height,
            );
            for (let i = 4; i < data.length; i += 4 * 97)
              for (let c = 0; c < 3; c += 1)
                if (data[i + c] !== data[c]) return true;
            return false;
          }),
        { timeout: 60_000 },
      )
      .toBe(true);
    await shots(page, `${reason}-waiting`);
    const stillWaiting = await lease();
    expect(stillWaiting?.id).toBe(waiting?.id);
    expect(stillWaiting?.wait_deadline).toBe(waiting?.wait_deadline);
    expect(stillWaiting?.active_s).toBe(waiting?.active_s);
    await clickInView(page, "#person-challenge");
    if (reason === "email_code") await typeInView(page, "482615");
    await clickInView(page, "#person-continue");
    await expect
      .poll(async () => (await application(job))?.state, { timeout: 240_000 })
      .toBe("ready");
    const app = (await application(job))!;
    const ready = await detail(app.id);
    expect(
      ready.answers.some(
        (row) => row.label === "First name" && row.value === "Morgan",
      ),
    ).toBe(true);
    expect(
      ready.answers.some(
        (row) => row.label === "Email" && row.value === person.email,
      ),
    ).toBe(true);
    expect(
      ready.answers
        .filter((row) => row.field_key.startsWith("designated:step:"))
        .map((row) => row.value),
    ).toEqual(["Save and Continue", "Save and Continue"]);
    expect(
      ready.answers.some((row) => row.field_key === "designated:submit"),
    ).toBe(true);
    expect(ready.events.filter((event) => event.kind === "lease")).toHaveLength(
      1,
    );
    expect(ready.events.some((event) => event.kind === "approval")).toBe(false);
    expect((await stub.submissions()).length).toBe(before);
    await expect(
      section.getByRole("button", { name: "Approve and submit", exact: true }),
    ).toBeVisible();
    await shots(page, `${reason}-ready`);
    await section
      .getByRole("button", { name: "Approve and submit", exact: true })
      .click();
    await page
      .getByRole("alertdialog")
      .getByRole("button", { name: "Approve and submit", exact: true })
      .click();
    await expect
      .poll(async () => (await application(job))?.state, { timeout: 240_000 })
      .toBe("submitted");
    const sent = await stub.submissions();
    expect(sent.length).toBe(before + 1);
    expect(sent.at(-1)?.org).toBe("a15-workday");
    expect(sent.at(-1)?.fields.email).toBe(person.email);
    expect(
      sent
        .at(-1)
        ?.files.map((file) => file.filename)
        .sort(),
    ).toEqual(["cv.pdf", "letter.pdf"]);
    const done = await detail(app.id);
    expect(done.events.filter((event) => event.kind === "click")).toHaveLength(
      1,
    );
    const currentApproval = await page.request.get(
      `/api/applications/${app.id}/approval`,
    );
    const { answers_hash } = (await currentApproval.json()) as {
      answers_hash: string;
    };
    const repeated = await page.request.post(
      `/api/applications/${app.id}/approve`,
      { headers: CSRF, data: { answers_hash } },
    );
    expect(repeated.status()).toBe(409);
    expect((await stub.submissions()).length).toBe(before + 1);
    await shots(page, `${reason}-submitted`);
  });
}
test("no page error was logged", () => {
  expect(errors).toEqual([]);
});
