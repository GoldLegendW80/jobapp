import { execFileSync } from "node:child_process";

import {
  expect,
  test,
  type BrowserContext,
  type Locator,
  type Page,
} from "@playwright/test";

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
import {
  clearAttempts,
  makeShots,
  newContext,
  root,
  watchErrors,
} from "./stack";

/** Real Lever fill, real API edits and real browser counters for #581 and #594. The unusual
 * original letter and terminal render error are seeded only into this invented user's local
 * fixture records. No approval or company Submit is clicked. Registered by apply-all.config. */
test.describe.configure({ mode: "serial", timeout: 600_000 });
test.skip(!caddyUrl, "set E2E_CADDY_URL with the Compose stack running");
const LEVER =
  "http://atsforms:8080/forms/a8/lever/example-robotics/applied-ai-engineer/apply.html";
const KEY = `sk-ant-api03-e2egood${"Ux58a".repeat(20)}Rn42`;
const shots = makeShots("apply-review-ux", "letter-editor");
let person: Person;
let context: BrowserContext;
let page: Page;
let errors: string[];
type Answer = {
  field_key: string;
  label: string;
  kind: string;
  value: unknown;
  source: string;
  max_chars: number;
  left_reason: string | null;
};
type Approval = { answers: Answer[] };

// The render timer's state transition is covered by the DB suite. Seed its terminal result
// here to prove the sentence through the real API and React, without advancing a real clock.
const SEED = `
import hashlib, json, sys, uuid
import sqlalchemy as sa
from app.config import get_settings
from app.db import build_engine, build_sessionmaker
from app.identity import service as identity
from app.apply.models import Application, State
from app.documents.models import Document, DocumentVersion, Kind
email, posting, variant = sys.argv[1], uuid.UUID(sys.argv[2]), sys.argv[3]
if not (email.startswith("e2e-") and email.endswith("@example.org")):
    raise SystemExit("refusing a non e2e account")
engine = build_engine(get_settings())
try:
    with build_sessionmaker(engine)() as db, db.begin():
        user = identity.get_user_by_email(db, email)
        if variant == "failed":
            app = db.scalar(sa.select(Application).where(Application.user_id == user.id, Application.posting_id == posting).with_for_update())
            assert app.state == State.cancelled
            app.state, app.error_code = State.failed, "documents_rendering"
        else:
            doc = db.scalar(sa.select(Document).where(Document.user_id == user.id, Document.posting_id == posting, Document.kind == Kind.letter))
            version = db.scalar(sa.select(DocumentVersion).where(DocumentVersion.document_id == doc.id).order_by(DocumentVersion.number.desc()).limit(1))
            assert version.frozen_at is None
            text = "L" * 10001 if variant == "long" else "Original letter\\twith a tab"
            version.content = {**version.content, "paragraphs": [text]}
            raw = json.dumps(version.content, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
            version.content_hash = hashlib.sha256(b"letter\\n" + raw).digest()
finally:
    engine.dispose()
`;

function seed(job: string, variant: "long" | "control" | "failed") {
  const project = process.env.E2E_COMPOSE_PROJECT;
  if (!project || process.env.E2E_ADMIN_VIA_DEPLOY)
    throw new Error(
      "this fixture requires an explicitly named local Compose project",
    );
  execFileSync(
    "docker",
    [
      "compose",
      "-p",
      project,
      "-f",
      "compose.yml",
      "exec",
      "-T",
      "api",
      "python",
      "-c",
      SEED,
      person.email,
      job,
      variant,
    ],
    { cwd: root, encoding: "utf8" },
  );
}

async function approval(id: string): Promise<Approval> {
  const response = await page.request.get(`/api/applications/${id}/approval`);
  expect(response.status()).toBe(200);
  return (await response.json()) as Approval;
}

async function posting(variant = "normal"): Promise<string> {
  const job = await addPosting(page.request, {
    ...person,
    posting: { ...person.posting, title: `${person.posting.title} ${variant}` },
  });
  const read = await page.request.get(`/api/jobs/${job}`);
  const { updated_at } = (await read.json()) as { updated_at: string };
  expect(
    (
      await page.request.patch(`/api/jobs/${job}`, {
        headers: CSRF,
        data: { url: LEVER, fit: 90, updated_at },
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

async function fill(job: string): Promise<string> {
  await page.goto(`/jobs/${job}`);
  const section = page.getByRole("region", { name: "Apply", exact: true });
  await section
    .getByRole("button", { name: "Apply (Review first)", exact: true })
    .click();
  await expect(
    section.getByRole("img", { name: /before the click/ }),
  ).toBeVisible({ timeout: 180_000 });
  const response = await page.request.get(
    `/api/applications?posting_id=${job}&limit=1`,
  );
  const { items } = (await response.json()) as { items: { id: string }[] };
  return items[0]!.id;
}

async function edit(answer: Answer): Promise<Locator> {
  const row = page
    .getByRole("region", { name: "Apply", exact: true })
    .getByRole("listitem")
    .filter({
      has: page
        .getByRole("button", {
          name: `Edit: ${answer.label}`,
          exact: true,
        })
        .or(
          page.getByRole("button", {
            name: `Stop editing: ${answer.label}`,
            exact: true,
          }),
        ),
    });
  await row
    .getByRole("button", { name: `Edit: ${answer.label}`, exact: true })
    .click();
  return row;
}

async function paste(box: Locator, text: string) {
  await box.fill("");
  await box.evaluate((element, value) => {
    const clipboardData = new DataTransfer();
    clipboardData.setData("text", value);
    element.dispatchEvent(
      new ClipboardEvent("paste", {
        bubbles: true,
        cancelable: true,
        clipboardData,
      }),
    );
  }, text);
}

async function cancel(id: string) {
  expect(
    (
      await page.request.post(`/api/applications/${id}/cancel`, {
        headers: CSRF,
      })
    ).status(),
  ).toBe(200);
}

test.beforeAll(async ({ browser }) => {
  person = invented("review-ux");
  context = await newContext(browser);
  page = await context.newPage();
  errors = watchErrors(page);
});
test.afterAll(async () => {
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

test("the trusted letter keeps its 10000 limit after repeated person edits", async () => {
  const job = await posting();
  const id = await fill(job);
  const answers = (await approval(id)).answers;
  const letter = answers.find((answer) => answer.max_chars === 10000)!;
  expect(letter.source).toBe("document");
  for (const size of [5001, 10000, 5001]) {
    const row = await edit(letter);
    const box = row.getByRole("textbox");
    const value = "L".repeat(size);
    await expect(box).toHaveAttribute("maxlength", "10000");
    await box.fill(value);
    await expect(row).toContainText(`${size} / 10000 characters`);
    await row.getByRole("button", { name: "Save answer", exact: true }).click();
    await expect
      .poll(async () =>
        (await approval(id)).answers.find(
          (one) => one.field_key === letter.field_key,
        ),
      )
      .toMatchObject({ value, source: "person", max_chars: 10000 });
  }
  const row = await edit(letter);
  await paste(row.getByRole("textbox"), "L".repeat(10001));
  await expect(row).toContainText("10001 / 10000 characters");
  await expect(row.getByRole("alert")).toHaveText(
    "Shorten this answer to 10000 characters before saving.",
  );
  await expect(
    row.getByRole("button", { name: "Save answer", exact: true }),
  ).toBeDisabled();
  await shots(page, "letter-overflow");
  await paste(row.getByRole("textbox"), "Letter\twith a tab");
  await expect(row.getByRole("alert")).toContainText("Remove tabs");
  await row
    .getByRole("button", { name: `Stop editing: ${letter.label}`, exact: true })
    .click();

  const ordinary = answers.find(
    (answer) =>
      answer.kind === "text" &&
      answer.max_chars === 5000 &&
      answer.source !== "left",
  )!;
  const field = await edit(ordinary);
  const box = field.getByRole("textbox");
  await expect(box).toHaveAttribute("maxlength", "5000");
  await box.fill("X".repeat(5000));
  await box.press("End");
  await box.press("X");
  await expect(box).toHaveValue("X".repeat(5000));
  await expect(field).toContainText("5000 / 5000 characters");
  await field.getByRole("button", { name: "Save answer", exact: true }).click();
  await expect
    .poll(
      async () =>
        (await approval(id)).answers.find(
          (one) => one.field_key === ordinary.field_key,
        )?.value,
    )
    .toBe("X".repeat(5000));
  expect(
    (
      await page.request.put(
        `/api/applications/${id}/answers/${encodeURIComponent(ordinary.field_key)}`,
        { headers: CSRF, data: { value: "X".repeat(5001) } },
      )
    ).status(),
  ).toBe(422);
  await cancel(id);
  seed(job, "failed");
  await page.reload();
  await expect(
    page.getByRole("region", { name: "Apply", exact: true }),
  ).toContainText("your CV or letter was still being prepared after an hour");
  await shots(page, "render-failed");
});

for (const [variant, reason, sentence] of [
  ["long", "letter_too_long", "The letter has more than 10000 characters"],
  [
    "control",
    "letter_control_character",
    "The letter contains a tab, carriage return or another control character",
  ],
] as const) {
  test(`the original ${variant} letter is left empty with its visible reason`, async () => {
    const job = await posting(variant);
    seed(job, variant);
    const id = await fill(job);
    const letter = (await approval(id)).answers.find(
      (answer) => answer.max_chars === 10000,
    )!;
    expect(letter).toMatchObject({ source: "left", left_reason: reason });
    expect(letter.value == null || letter.value === "").toBe(true);
    const box = page.getByRole("textbox", {
      name: `Answer for ${letter.label}`,
      exact: true,
    });
    await expect(box).toHaveValue("");
    await expect(box).toHaveAttribute("maxlength", "10000");
    await expect(page.getByText(sentence, { exact: false })).toBeVisible();
    await shots(page, `original-${variant}`);
    await cancel(id);
  });
}

test("no company submission or browser console error occurred", async () => {
  expect(await stub.submissions()).toEqual([]);
  expect(errors).toEqual([]);
});
