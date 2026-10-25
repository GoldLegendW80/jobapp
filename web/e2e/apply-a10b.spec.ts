import { expect, test, type BrowserContext, type Page } from "@playwright/test";

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

const shots = makeShots("apply-a10b", "signin");
const KEY = `sk-ant-api03-e2egood${"Qa10x".repeat(20)}Rn42`;
let person: Person;
let context: BrowserContext;
let page: Page;
let errors: string[];

async function posting(number: string): Promise<string> {
  const job = await addPosting(page.request, {
    ...person,
    posting: { ...person.posting, title: `${person.posting.title} ${number}` },
  });
  const read = await page.request.get(`/api/jobs/${job}`);
  const { updated_at } = (await read.json()) as { updated_at: string };
  expect(
    (
      await page.request.patch(`/api/jobs/${job}`, {
        headers: CSRF,
        data: {
          url: `http://atsforms:8080/greenhouse/northwind-labs/jobs/${number}--signin`,
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
        const response = await page.request.get(`/api/jobs/${job}/documents`);
        const { documents } = (await response.json()) as {
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

async function app(
  job: string,
): Promise<{ id: string; state: string; needs: string | null; mode: string }> {
  const read = await page.request.get(
    `/api/applications?posting_id=${job}&limit=1`,
  );
  const { items } = (await read.json()) as {
    items: { id: string; state: string; needs: string | null; mode: string }[];
  };
  if (!items[0]) throw new Error("No application was created.");
  return items[0];
}

async function signIn(later: boolean): Promise<void> {
  const banner = page.getByRole("region", {
    name: "Sign in to atsforms",
    exact: true,
  });
  await expect(banner).toBeVisible({ timeout: 180_000 });
  const open = banner.getByRole("button", {
    name: later ? "Open the live view to sign in" : "Open the live view",
    exact: true,
  });
  if (await open.count()) await open.click();
  await expect
    .poll(
      async () =>
        banner.getByTestId("live-screen").evaluate((screen) => {
          const canvas = screen.querySelector("canvas");
          const context = canvas?.getContext("2d");
          if (!canvas || !context || canvas.width === 0) return false;
          const { data } = context.getImageData(
            0,
            0,
            canvas.width,
            canvas.height,
          );
          for (let i = 4; i < data.length; i += 4 * 97) {
            for (let c = 0; c < 3; c += 1)
              if (data[i + c] !== data[c]) return true;
          }
          return false;
        }),
      { timeout: 60_000 },
    )
    .toBe(true);
  await banner
    .getByRole("button", { name: "Keyboard", exact: true })
    .click({ timeout: 60_000 });
  const keyboard = banner.getByLabel("Type into your browser");
  await keyboard.pressSequentially("fixture-word");
  await keyboard.press("Enter");
  await shots(page, later ? "later-session" : "watched-handoff");
  await banner
    .getByRole("button", { name: "I'm signed in", exact: true })
    .click();
}

test.beforeAll(async ({ browser }) => {
  person = invented("a10b");
  context = await newContext(browser);
  page = await context.newPage();
  errors = watchErrors(page);
});

test.afterAll(async () => {
  await page.request.post("/api/browser/handoff/cancel", { headers: CSRF });
  await page.request.delete("/api/browser/sessions/current", { headers: CSRF });
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
  const consent = await page.request.get("/api/documents/consent");
  const { version } = (await consent.json()) as { version: number };
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

test("a watched login wall resumes and submits only after approval", async () => {
  const job = await posting("4012345");
  await page.goto(`/jobs/${job}`);
  const section = page.getByRole("region", { name: "Apply", exact: true });
  await section
    .getByRole("button", { name: "Apply (Review first)", exact: true })
    .click();
  await signIn(false);
  await expect
    .poll(async () => (await app(job)).state, { timeout: 180_000 })
    .toBe("ready");
  expect(await stub.submissions()).toHaveLength(0);
  await section
    .getByRole("button", { name: "Approve and submit", exact: true })
    .click();
  await page
    .getByRole("alertdialog")
    .getByRole("button", { name: "Approve and submit", exact: true })
    .click();
  await expect
    .poll(async () => (await app(job)).state, { timeout: 180_000 })
    .toBe("submitted");
  expect(await stub.submissions()).toHaveLength(1);
  await shots(page, "submitted-after-approval");
});

test("an unwatched Like waits, then a later sign in queues a review fill", async () => {
  const before = (await stub.submissions()).length;
  const job = await posting("4012346");
  await page.goto("/jobs");
  expect(
    (
      await page.request.post(`/api/postings/${job}/like`, { headers: CSRF })
    ).status(),
  ).toBe(201);
  await expect
    .poll(async () => (await app(job)).needs, { timeout: 180_000 })
    .toBe("signin");
  await page.goto(`/jobs/${job}`);
  await signIn(true);
  await expect
    .poll(async () => (await app(job)).state, { timeout: 180_000 })
    .toBe("ready");
  expect((await app(job)).mode).toBe("review");
  expect(await stub.submissions()).toHaveLength(before);
  await shots(page, "later-ready-for-approval");
  expect(errors).toEqual([]);
});
