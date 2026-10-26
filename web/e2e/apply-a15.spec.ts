import { expect, test, type BrowserContext, type Page } from "@playwright/test";

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

const shots = makeShots("apply-a15", "wizards");
const KEY = `sk-ant-api03-e2egood${"Qa15x".repeat(20)}Rn42`;
let person: Person;
let context: BrowserContext;
let page: Page;
let errors: string[];

async function posting(
  kind: string,
  number: string,
  variant = "",
): Promise<string> {
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
          url: `http://atsforms:8080/forms/a15/${kind}.html?job=${number}${variant ? `&variant=${variant}` : ""}`,
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

async function app(
  job: string,
): Promise<
  { id: string; state: string; needs: string | null; mode: string } | undefined
> {
  const read = await page.request.get(
    `/api/applications?posting_id=${job}&limit=1`,
  );
  const { items } = (await read.json()) as {
    items: { id: string; state: string; needs: string | null; mode: string }[];
  };
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
  person = invented("a15");
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

async function reviewShot(name: string): Promise<void> {
  await expect(
    page
      .getByRole("region", { name: "Apply", exact: true })
      .getByRole("button", { name: "Approve and submit", exact: true }),
  ).toBeVisible();
  await shots(page, name);
}

async function approveAndSubmit(job: string): Promise<void> {
  const section = page.getByRole("region", { name: "Apply", exact: true });
  await section
    .getByRole("button", { name: "Approve and submit", exact: true })
    .click();
  await page
    .getByRole("alertdialog")
    .getByRole("button", {
      name: "Approve and submit",
      exact: true,
    })
    .click();
  await expect
    .poll(async () => (await app(job))?.state, { timeout: 240_000 })
    .toBe("submitted");
}

test("heavy Workday account handoff and ordered wizard submit once after approval", async ({
  browser,
}, testInfo) => {
  expect(browser.isConnected()).toBe(true);
  const before = (await stub.submissions()).length;
  const job = await posting("workday", "1001");
  await page.goto(`/jobs/${job}`);
  const began = Date.now();
  await page
    .getByRole("region", { name: "Apply", exact: true })
    .getByRole("button", { name: "Apply (Review first)", exact: true })
    .click();
  await signIn(false);
  const activeSamples: number[] = [];
  await expect
    .poll(
      async () => {
        const current = await page.request.get("/api/browser");
        if (current.ok()) {
          const view = (await current.json()) as {
            lease: { purpose: string; active_s: number } | null;
          };
          if (view.lease?.purpose === "apply_fill")
            activeSamples.push(view.lease.active_s);
        }
        return (await app(job))?.state;
      },
      { timeout: 300_000 },
    )
    .toBe("ready");
  const upperBoundSeconds = (Date.now() - began) / 1000;
  await testInfo.attach("workday-human-paced-duration", {
    body: JSON.stringify({
      wall_seconds_including_handoff: upperBoundSeconds,
      sampled_active_seconds: activeSamples,
      heavy_fixture_mib: 48,
      lease_max_unchanged_s: 900,
    }),
    contentType: "application/json",
  });
  console.log(
    `A15 Workday active samples: ${activeSamples.join(",")}; wall including handoff: ${upperBoundSeconds}s`,
  );
  expect((await stub.submissions()).length).toBe(before);
  await reviewShot("workday-review");
  await approveAndSubmit(job);
  const submitted = await stub.submissions();
  expect(submitted.length).toBe(before + 1);
  expect(submitted.at(-1)?.org).toBe("a15-workday");
  expect(
    submitted
      .at(-1)
      ?.files.map((file) => file.filename)
      .sort(),
  ).toEqual(["cv.pdf", "letter.pdf"]);
  expect(submitted.at(-1)?.fields.email).toBe(person.email);
  await shots(page, "workday-submitted");
});

for (const kind of ["google_careers", "vie"]) {
  test(`${kind} uses labelled native fields and document uploads`, async () => {
    const before = (await stub.submissions()).length;
    const job = await posting(kind, kind === "vie" ? "1003" : "1002");
    await page.goto(`/jobs/${job}`);
    await page
      .getByRole("region", { name: "Apply", exact: true })
      .getByRole("button", { name: "Apply (Review first)", exact: true })
      .click();
    await expect
      .poll(async () => (await app(job))?.state, { timeout: 240_000 })
      .toBe("ready");
    expect((await stub.submissions()).length).toBe(before);
    await reviewShot(`${kind}-review`);
    await approveAndSubmit(job);
    const submitted = await stub.submissions();
    expect(submitted.length).toBe(before + 1);
    expect(submitted.at(-1)?.org).toBe(`a15-${kind}`);
    expect(submitted.at(-1)?.fields.email).toBe(person.email);
    if (kind === "google_careers")
      expect(submitted.at(-1)?.fields.first_name).not.toBe(
        "Parser guessed this",
      );
    if (kind === "vie")
      expect(submitted.at(-1)?.fields.motivation).toBe(
        "I build platforms that other engineers rely on.",
      );
  });
}

test("an unknown required early field resumes all remaining steps before approval", async () => {
  const before = (await stub.submissions()).length;
  const job = await posting("workday", "1004", "required");
  await page.goto(`/jobs/${job}`);
  await page
    .getByRole("region", { name: "Apply", exact: true })
    .getByRole("button", { name: "Apply (Review first)", exact: true })
    .click();
  await expect
    .poll(async () => (await app(job))?.needs, { timeout: 180_000 })
    .toBe("required_left");
  const current = await app(job);
  expect(current).toBeDefined();
  const read = await page.request.get(
    `/api/applications/${current!.id}/approval`,
  );
  const view = (await read.json()) as {
    answers: { field_key: string; label: string }[];
  };
  const field = view.answers.find(
    (row) => row.label === "Your project codename",
  );
  expect(field).toBeTruthy();
  const answer = await page.request.put(
    `/api/applications/${current!.id}/answers/${encodeURIComponent(field!.field_key)}`,
    {
      headers: CSRF,
      data: { value: "Example Cedar" },
    },
  );
  expect(answer.status()).toBe(200);
  await expect
    .poll(async () => (await app(job))?.state, { timeout: 240_000 })
    .toBe("ready");
  expect((await stub.submissions()).length).toBe(before);
  // The answer went through the api, not the page, so the page has not seen the refill start
  // (it polls only while a fill or a submit runs): read it again.
  await page.reload();
  await approveAndSubmit(job);
  const submitted = await stub.submissions();
  expect(submitted.length).toBe(before + 1);
  expect(submitted.at(-1)?.fields.codename).toBe("Example Cedar");
  expect(errors).toEqual([]);
});
