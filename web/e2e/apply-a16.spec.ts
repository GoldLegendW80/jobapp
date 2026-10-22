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

// Only synthetic local ATS forms. Browserd uses its private opener; the model click path is unchanged.
test.describe.configure({ mode: "serial", timeout: 900_000 });
test.skip(!caddyUrl, "set E2E_CADDY_URL with the Compose stack running");
const KEY = `sk-ant-api03-e2egood${"Qa16x".repeat(20)}Rn42`;
const shots = makeShots("apply-a16", "linkedin");
const FILL = 420_000;
let context: BrowserContext;
let page: Page;
let person: Person;
let errors: string[];
let review: string;

type Application = {
  id: string;
  state: string;
  mode: string;
  needs: string | null;
};
async function latest(job: string): Promise<Application> {
  const response = await page.request.get(
    `/api/applications?posting_id=${job}&limit=1`,
  );
  const { items } = (await response.json()) as { items: Application[] };
  return items[0] ?? { id: "", state: "absent", mode: "", needs: null };
}
async function posting(number: number, variant = "plain") {
  const id = await addPosting(page.request, {
    ...person,
    posting: { ...person.posting, title: `${person.posting.title} ${number}` },
  });
  const existing = await page.request.get(`/api/jobs/${id}`);
  const { updated_at } = (await existing.json()) as { updated_at: string };
  expect(
    (
      await page.request.patch(`/api/jobs/${id}`, {
        headers: CSRF,
        data: {
          updated_at,
          fit: 90,
          url: `http://atsforms:8080/linkedin/jobs/view/${number}/?variant=${variant}`,
        },
      })
    ).status(),
  ).toBe(200);
  expect(
    (
      await page.request.post(`/api/jobs/${id}/documents/write`, {
        headers: CSRF,
        data: { kinds: ["cv", "letter"], script: "journey" },
      })
    ).status(),
  ).toBe(202);
  await expect
    .poll(
      async () => {
        const read = await page.request.get(`/api/jobs/${id}/documents`);
        const { documents } = (await read.json()) as {
          documents: { latest: unknown }[];
        };
        return (
          documents.length === 2 && documents.every((d) => d.latest !== null)
        );
      },
      { timeout: 150_000 },
    )
    .toBe(true);
  return id;
}
const accepted = async () =>
  (await stub.submissions()).filter((s) => s.org === "linkedin");

async function address(job: string, url: string) {
  const read = await page.request.get(`/api/jobs/${job}`);
  const { updated_at } = (await read.json()) as { updated_at: string };
  expect(
    (
      await page.request.patch(`/api/jobs/${job}`, {
        headers: CSRF,
        data: { updated_at, url },
      })
    ).status(),
  ).toBe(200);
}

test.beforeAll(async ({ browser }) => {
  person = invented("a16");
  context = await newContext(browser);
  page = await context.newPage();
  errors = watchErrors(page);
});
test.afterAll(async () => {
  await context.close();
  clearAttempts([person.email]);
});

test("without consent an external link starts no application, then the person opts in", async () => {
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
  review = await posting(4100000101);
  // This synthetic address is displayed only. No browser lease or site navigation starts.
  await address(review, "https://www.linkedin.com/jobs/view/4100000101/");
  await page.goto(`/jobs/${review}`);
  await expect(page.getByText(/Turn on LinkedIn in Account/)).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Apply (Review first)" }),
  ).toHaveCount(0);
  const external = page.getByRole("link", {
    name: "Open LinkedIn and apply myself",
  });
  await expect(external).toHaveAttribute(
    "href",
    "https://www.linkedin.com/jobs/view/4100000101/",
  );
  await expect(
    page.getByRole("button", { name: "Apply myself", exact: true }),
  ).toHaveCount(0);
  await external.evaluate((link) => {
    link.addEventListener("click", (event) => {
      event.preventDefault();
    });
  });
  await external.click();
  expect((await latest(review)).state).toBe("absent");
  const read = await page.request.get("/api/browser/linkedin");
  const { text_version } = (await read.json()) as { text_version: number };
  expect(
    (
      await page.request.put("/api/browser/linkedin", {
        headers: CSRF,
        data: {
          enabled: true,
          accept_version: text_version,
          query: "Robotics Engineer",
        },
      })
    ).status(),
  ).toBe(200);
  await address(review, "http://atsforms:8080/linkedin/jobs/view/4100000101/");
});

test("Review fills the modal, declines Follow company, and submits once after approval", async () => {
  await page.goto(`/jobs/${review}`);
  await page.getByRole("button", { name: "Apply (Review first)" }).click();
  await expect
    .poll(async () => (await latest(review)).state, { timeout: FILL })
    .toBe("ready");
  await page.reload();
  await expect(
    page.getByText("Follow this company", { exact: true }).first(),
  ).toBeVisible();
  expect(await accepted()).toHaveLength(0);
  await shots(page, "review");
  await page
    .getByRole("button", { name: "Approve and submit", exact: true })
    .click();
  await page
    .getByRole("alertdialog")
    .getByRole("button", { name: "Approve and submit" })
    .click();
  await expect
    .poll(async () => (await latest(review)).state, { timeout: FILL })
    .toBe("submitted");
  const sent = await accepted();
  expect(sent).toHaveLength(1);
  expect(sent[0]!.fields).not.toHaveProperty("follow_company");
});

test("three rehearsals send nothing, then Full auto holds and submits exactly once", async () => {
  await page.setViewportSize({ width: 1280, height: 800 });
  await page.emulateMedia({ colorScheme: "light" });
  expect(
    (
      await page.request.put("/api/apply/settings", {
        headers: CSRF,
        data: { mode_after_like: "full_auto" },
      })
    ).status(),
  ).toBe(200);
  const cap = await page.request.get("/api/apply/caps");
  const capData = (await cap.json()) as { revision: string };
  expect(
    (
      await page.request.put("/api/apply/caps", {
        headers: CSRF,
        data: {
          revision: capData.revision,
          items: [
            {
              label: person.posting.company,
              max_count: 5,
              window_days: 30,
              same_role_days: null,
            },
          ],
        },
      })
    ).status(),
  ).toBe(200);
  for (const number of [4100000102, 4100000103, 4100000104]) {
    const id = await posting(number);
    expect(
      (
        await page.request.post(`/api/postings/${id}/like`, { headers: CSRF })
      ).status(),
    ).toBe(201);
    await expect
      .poll(async () => (await latest(id)).state, { timeout: FILL })
      .toBe("rehearsed");
    expect(await accepted()).toHaveLength(1);
    const app = await latest(id);
    expect(
      (
        await page.request.post(`/api/applications/${app.id}/rehearsal`, {
          headers: CSRF,
          data: { answer: "yes" },
        })
      ).status(),
    ).toBe(200);
  }
  const id = await posting(4100000105);
  expect(
    (
      await page.request.post(`/api/postings/${id}/like`, { headers: CSRF })
    ).status(),
  ).toBe(201);
  await expect
    .poll(async () => (await latest(id)).state, { timeout: FILL })
    .toBe("held");
  await page.goto("/applications");
  const heldCard = page.getByRole("article", {
    name: `${person.posting.title} 4100000105`,
    exact: true,
  });
  await expect(heldCard).toContainText(
    "Full auto: your Like approved it. It is held so you can cancel, then checked once more and submitted.",
  );
  await expect(heldCard.getByRole("timer")).toBeVisible();
  await shots(page, "held");
  await expect
    .poll(async () => (await latest(id)).state, { timeout: FILL })
    .toBe("submitted");
  const sent = await accepted();
  expect(sent).toHaveLength(2);
  expect(sent.every((s) => !("follow_company" in s.fields))).toBe(true);
  const app = await latest(id),
    read = await page.request.get(`/api/applications/${app.id}`);
  const { events } = (await read.json()) as {
    events: { kind: string; at: string; data: Record<string, unknown> }[];
  };
  const held = events.find(
    (e) => e.kind === "decision" && e.data.outcome === "held",
  );
  const clicks = events.filter((e) => e.kind === "click");
  expect(clicks).toHaveLength(1);
  expect(Date.parse(clicks[0]!.at)).toBeGreaterThanOrEqual(
    Date.parse(String(held!.data.until)),
  );
});

test("unknown questions and expired sign-in go to review, company links use their adapter", async () => {
  for (const [number, variant, needs] of [
    [4100000106, "unknown", "required_left"],
    [4100000107, "signin", "signin"],
  ] as const) {
    const id = await posting(number, variant);
    expect(
      (
        await page.request.post(`/api/postings/${id}/like`, { headers: CSRF })
      ).status(),
    ).toBe(201);
    await expect
      .poll(async () => (await latest(id)).state, { timeout: FILL })
      .toBe("needs_person");
    expect(await latest(id)).toMatchObject({ mode: "review", needs });
    if (variant === "unknown") {
      await page.goto(`/jobs/${id}`);
      const input = page.getByRole("textbox", {
        name: /^Answer for How many moon landings/,
      });
      await input.fill("0");
      const row = page.getByRole("listitem").filter({ has: input });
      await row.getByRole("button", { name: "Save answer" }).click();
      await expect
        .poll(async () => (await latest(id)).state, { timeout: FILL })
        .toBe("ready");
      await page.reload();
      const resume = page
        .getByRole("list", { name: "Fields filled" })
        .getByRole("listitem")
        .filter({ hasText: "Resume (required)" });
      await expect(resume.getByText("cv.pdf", { exact: true })).toBeVisible();
      expect(await accepted()).toHaveLength(2);
      await page
        .getByRole("button", { name: "Approve and submit", exact: true })
        .click();
      await page
        .getByRole("alertdialog")
        .getByRole("button", { name: "Approve and submit" })
        .click();
      await expect
        .poll(async () => (await latest(id)).state, { timeout: FILL })
        .toBe("submitted");
      expect(await accepted()).toHaveLength(3);
    }
  }
  const id = await posting(4100000108, "company");
  expect(
    (
      await page.request.post(`/api/postings/${id}/applications`, {
        headers: CSRF,
        data: { mode: "review", dry_run: false },
      })
    ).status(),
  ).toBe(201);
  await expect
    .poll(async () => (await latest(id)).state, { timeout: FILL })
    .toBe("ready");
  const app = await latest(id),
    read = await page.request.get(`/api/applications/${app.id}`);
  const result = (await read.json()) as {
    ats: string;
    form_url: string;
  };
  expect(result).toMatchObject({ ats: "greenhouse" });
  expect(result.form_url).toContain("/greenhouse/");
  expect(await accepted()).toHaveLength(3);
});

test("a checkpoint pauses LinkedIn for seven days and moves held work to review", async () => {
  const heldJob = await posting(4100000109),
    checkpoint = await posting(4100000110, "checkpoint");
  expect(
    (
      await page.request.post(`/api/postings/${heldJob}/like`, {
        headers: CSRF,
      })
    ).status(),
  ).toBe(201);
  await expect
    .poll(async () => (await latest(heldJob)).state, { timeout: FILL })
    .toBe("held");
  expect(
    (
      await page.request.post(`/api/postings/${checkpoint}/like`, {
        headers: CSRF,
      })
    ).status(),
  ).toBe(201);
  await expect
    .poll(async () => (await latest(checkpoint)).needs, { timeout: FILL })
    .toBe("linkedin_paused");
  expect(await latest(heldJob)).toMatchObject({
    state: "ready",
    mode: "review",
  });
  const read = await page.request.get("/api/browser/linkedin");
  const state = (await read.json()) as {
    allowed: boolean;
    paused_until: string;
  };
  expect(state.allowed).toBe(false);
  expect(Date.parse(state.paused_until)).toBeGreaterThan(
    Date.now() + 6 * 24 * 3600 * 1000,
  );
  expect(await accepted()).toHaveLength(3);
  await page.goto("/applications");
  const waiting = page.getByRole("region", { name: /^Waiting for you/ });
  const returned = waiting.getByRole("article", {
    name: `${person.posting.title} 4100000109`,
    exact: true,
  });
  await expect(returned).toContainText(
    "Filled and waiting for your approval. Nothing is sent until you approve.",
  );
  await expect(returned).toContainText(
    "Why it came back: LinkedIn is paused for a week after a security check. Nothing was sent.",
  );
  await expect(
    returned.getByRole("button", { name: "Review the answers", exact: true }),
  ).toBeVisible();
  const stopped = waiting.getByRole("article", {
    name: `${person.posting.title} 4100000110`,
    exact: true,
  });
  await expect(stopped).toContainText(
    "The form needs you before it can go on. Nothing was sent.",
  );
  await shots(page, "checkpoint-review");
});

test("no browser error was logged", () => {
  expect(errors).toEqual([]);
});
