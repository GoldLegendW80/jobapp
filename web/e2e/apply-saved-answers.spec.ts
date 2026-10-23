import { expect, test, type Page } from "@playwright/test";

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
import { clearAttempts, makeShots, newContext, watchErrors } from "./stack";

// Only an invented account, the fake model backend and the local ATS fixture are used.
// Saved answers are created through the visible Settings form, with no database seed.
test.describe.configure({ timeout: 600_000 });
test.skip(!caddyUrl, "set E2E_CADDY_URL with the Compose stack running");
const QUESTION = "How did you hear about this job?";
const KEY = `sk-ant-api03-e2egood${"Dn9x".repeat(20)}Rn42`;
const shots = makeShots("apply-saved-answers", "saved-answers");

type Approval = {
  answers: Array<{
    field_key: string;
    label: string;
    value: unknown;
    source: string;
  }>;
};

async function add(page: Page, question: string, answer: string) {
  await page.getByRole("button", { name: "Add answer", exact: true }).click();
  await expect(
    page.getByRole("textbox", { name: "Question", exact: true }),
  ).toBeFocused();
  await page
    .getByRole("textbox", { name: "Question", exact: true })
    .fill(question);
  await page.getByRole("textbox", { name: "Reusable answer" }).fill(answer);
  await page.getByRole("button", { name: "Save new answer" }).click();
  await expect(
    page.getByRole("form", { name: "Add a saved answer" }),
  ).toHaveCount(0);
}

async function fill(
  page: Page,
  person: Person,
  variant: string,
): Promise<{ id: string; approval: Approval }> {
  const job = await addPosting(page.request, {
    ...person,
    posting: {
      ...person.posting,
      title: person.posting.title + (variant ? ", Runtime" : ""),
    },
  });
  const read = await page.request.get(`/api/jobs/${job}`);
  const { updated_at } = (await read.json()) as { updated_at: string };
  expect(
    (
      await page.request.patch(`/api/jobs/${job}`, {
        headers: CSRF,
        data: {
          url: (await stub.posting("greenhouse", variant)).replace(
            atsFormsUrl ?? "",
            "http://atsforms:8080",
          ),
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
        const body = (await response.json()) as {
          documents: Array<{ latest: unknown }>;
        };
        return (
          body.documents.length === 2 &&
          body.documents.every((document) => document.latest !== null)
        );
      },
      { timeout: 150_000 },
    )
    .toBe(true);
  const started = await page.request.post(`/api/postings/${job}/applications`, {
    headers: CSRF,
    data: { mode: "review" },
  });
  expect(started.status()).toBe(201);
  const body = (await started.json()) as { id: string };
  await page.goto(`/jobs/${job}`);
  await expect(
    page
      .getByRole("region", { name: "Apply" })
      .getByRole("img", { name: /before the click/ }),
  ).toBeVisible({ timeout: 240_000 });
  const response = await page.request.get(
    `/api/applications/${body.id}/approval`,
  );
  expect(response.status()).toBe(200);
  return { id: body.id, approval: (await response.json()) as Approval };
}

test("a fresh person adds reusable answers, sees worker reuse and keeps application edits local", async ({
  browser,
}) => {
  const person = invented("saved-answers");
  const context = await newContext(browser);
  const page = await context.newPage();
  const errors = watchErrors(page);
  try {
    await stub.reset();
    await signUp(page, person);
    await page.goto("/settings/applications");
    await expect(page.getByText("No saved answer yet.")).toBeVisible();
    await add(page, QUESTION, "Referral");
    await add(page, "Email", "e2e-bank@example.org");
    await expect(
      page.getByText("Reusable answer saved.", { exact: true }),
    ).toHaveCount(0);
    await page.getByRole("button", { name: "Add answer", exact: true }).click();
    await page
      .getByRole("textbox", { name: "Question", exact: true })
      .fill("An optional question?");
    await page
      .getByRole("textbox", { name: "Reusable answer" })
      .fill("An explicit reusable answer");
    await shots(page, "add-answer");
    await page.getByRole("button", { name: "Cancel", exact: true }).click();

    expect(
      (
        await page.request.put("/api/me/model-credential", {
          headers: CSRF,
          data: { kind: "anthropic_api_key", secret: KEY },
        })
      ).status(),
    ).toBe(200);
    const consent = (await (
      await page.request.get("/api/documents/consent")
    ).json()) as { version: number };
    expect(
      (
        await page.request.post("/api/documents/consent", {
          headers: CSRF,
          data: { version: consent.version },
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
    const first = await fill(page, person, "");
    const heard = first.approval.answers.find(
      (answer) => answer.label === QUESTION,
    );
    expect(heard).toMatchObject({ value: "Referral", source: "bank" });
    expect(
      first.approval.answers.find((answer) => answer.label === "Email"),
    ).toMatchObject({ value: person.email, source: "identity" });
    expect(heard).toBeDefined();
    expect(
      (
        await page.request.put(
          `/api/applications/${first.id}/answers/${encodeURIComponent(heard!.field_key)}`,
          { headers: CSRF, data: { value: "Other" } },
        )
      ).status(),
    ).toBe(200);
    const bank = (await (
      await page.request.get("/api/apply/answer-bank")
    ).json()) as {
      items: Array<{ question: string; answer: string; used_count: number }>;
    };
    expect(
      bank.items.find((entry) => entry.question === QUESTION),
    ).toMatchObject({ answer: "Referral", used_count: 1 });
    expect(
      bank.items.find((entry) => entry.question === "Email"),
    ).toMatchObject({ answer: "e2e-bank@example.org", used_count: 0 });
    const profile = (await (await page.request.get("/api/profile")).json()) as {
      revision: number;
      sections: { answers: Record<string, unknown> };
    };
    expect(
      (
        await page.request.put("/api/profile/answers", {
          headers: CSRF,
          data: {
            base_revision: profile.revision,
            data: {
              ...profile.sections.answers,
              heard_about: "Company website",
            },
          },
        })
      ).status(),
    ).toBe(200);
    const second = await fill(page, person, "slow");
    expect(
      second.approval.answers.find((answer) => answer.label === QUESTION),
    ).toMatchObject({ value: "Company website", source: "answers" });
    expect(await stub.submissions()).toEqual([]);
    expect(errors).toEqual([]);
  } finally {
    await context.close();
    clearAttempts([person.email]);
  }
});
