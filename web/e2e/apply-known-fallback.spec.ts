import { execFileSync } from "node:child_process";

import { createHash } from "node:crypto";

import { expect, test, type BrowserContext, type Page } from "@playwright/test";

import { waitForPostingDocuments } from "./apply-document-helpers";
import { atsFormsUrl, stub, type Submission } from "./apply-helpers";
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

// Real worker, browser, documents and approval gate. Only the external ATS and model are fakes.
test.describe.configure({ mode: "serial", timeout: 900_000 });
test.skip(!caddyUrl, "set E2E_CADDY_URL with the Compose stack running");
const STACK_FORMS = "http://atsforms:8080";
const KEY = `sk-ant-api03-e2egood${"Fn63".repeat(20)}Rn42`;
const SUMMARY = "I build platforms that other engineers rely on.";
const FILL = 360_000;
const shots = makeShots("apply-known-fallback", "known-fallback");

type App = {
  id: string;
  state: string;
  mode: string;
  needs: string | null;
  ats: string;
};
type Answer = {
  field_key: string;
  label: string;
  value: unknown;
  source: string;
  kind: string;
};
type Approval = { answers: Answer[] };
type Audit = {
  strategy: Record<string, unknown> | null;
  cost: Record<string, unknown>;
  cv: { document_id: string; number: number };
};

// Read only, scoped to this invented application. Public views intentionally hide replay metadata.
function audit(id: string): Audit {
  if (!/^[0-9a-f-]{36}$/.test(id)) throw new Error("not an application id");
  const code = [
    "import json, sys, uuid, sqlalchemy as sa",
    "from app.config import Settings",
    "from app.db import build_engine, build_sessionmaker",
    "from app.apply.models import Application, ApplicationAnswer",
    "with build_sessionmaker(build_engine(Settings()))() as db:",
    " app = db.get(Application, uuid.UUID(sys.argv[1]))",
    " strategy = db.scalar(sa.select(ApplicationAnswer).where(ApplicationAnswer.application_id == app.id, ApplicationAnswer.field_key == 'designated:strategy'))",
    " print(json.dumps({'strategy': strategy.value if strategy else None, 'cost': app.cost, 'cv': app.document_versions.get('cv')}))",
  ].join("\n");
  return JSON.parse(
    execFileSync(
      "docker",
      [
        "compose",
        "-p",
        process.env.E2E_COMPOSE_PROJECT ?? "jobapp",
        "-f",
        "compose.yml",
        "exec",
        "-T",
        "api",
        "python",
        "-c",
        code,
        id,
      ],
      { cwd: root, encoding: "utf8" },
    ),
  ) as Audit;
}
let person: Person;
let context: BrowserContext;
let page: Page;
let errors: string[];
let serial = 0;
let fullAutoJob: { id: string; title: string };

async function latest(where: Page, job: string): Promise<App> {
  const response = await where.request.get(
    `/api/applications?posting_id=${job}&limit=1`,
  );
  expect(response.status()).toBe(200);
  const { items } = (await response.json()) as { items: App[] };
  return (
    items[0] ?? { id: "", state: "absent", mode: "", needs: null, ats: "" }
  );
}

async function approval(where: Page, id: string): Promise<Approval> {
  const response = await where.request.get(`/api/applications/${id}/approval`);
  expect(response.status()).toBe(200);
  return (await response.json()) as Approval;
}

async function prepare(where: Page, who: Person) {
  await signUp(where, who);
  expect(
    (
      await where.request.put("/api/me/model-credential", {
        headers: CSRF,
        data: { kind: "anthropic_api_key", secret: KEY },
      })
    ).status(),
  ).toBe(200);
  const { version } = (await (
    await where.request.get("/api/documents/consent")
  ).json()) as { version: number };
  expect(
    (
      await where.request.post("/api/documents/consent", {
        headers: CSRF,
        data: { version },
      })
    ).status(),
  ).toBe(200);
  const built = await where.request.post("/api/documents/base-cvs", {
    headers: CSRF,
    data: { from: "profile", script: "documents_base_journey" },
  });
  expect(built.status()).toBe(202);
  const { base_cv } = (await built.json()) as { base_cv: { id: string } };
  await waitForRender(where.request, base_cv.id, 1);
  const profile = (await (await where.request.get("/api/profile")).json()) as {
    revision: number;
    sections: { answers: Record<string, unknown> };
  };
  expect(
    (
      await where.request.put("/api/profile/answers", {
        headers: CSRF,
        data: {
          base_revision: profile.revision,
          data: { ...profile.sections.answers, heard_about: "Company website" },
        },
      })
    ).status(),
  ).toBe(200);
}

async function posting(
  where: Page,
  who: Person,
  url: string,
): Promise<{ id: string; title: string }> {
  const title = `${who.posting.title}, Fallback ${++serial}`;
  const id = await addPosting(where.request, {
    ...who,
    posting: { ...who.posting, title },
  });
  const { updated_at } = (await (
    await where.request.get(`/api/jobs/${id}`)
  ).json()) as { updated_at: string };
  expect(
    (
      await where.request.patch(`/api/jobs/${id}`, {
        headers: CSRF,
        data: { url, fit: 90, updated_at },
      })
    ).status(),
  ).toBe(200);
  expect(
    (
      await where.request.post(`/api/jobs/${id}/documents/write`, {
        headers: CSRF,
        data: { kinds: ["cv", "letter"], script: "journey" },
      })
    ).status(),
  ).toBe(202);
  await waitForPostingDocuments(where.request, id);
  return { id, title };
}

async function hosted(where: Page, who: Person) {
  const url = (await stub.posting("ashby"))
    .replace(atsFormsUrl ?? "", STACK_FORMS)
    .replace("/application", "--known-fallback/application");
  return posting(where, who, url);
}

async function start(where: Page, id: string) {
  expect(
    (
      await where.request.post(`/api/postings/${id}/applications`, {
        headers: CSRF,
        data: { mode: "review" },
      })
    ).status(),
  ).toBe(201);
}

const sent = async () =>
  (await stub.submissions()).filter(
    (one) => one.org === "custom-known-fallback",
  );

async function pinnedCv(where: Page, app: string) {
  const cv = audit(app).cv;
  const pdf = await where.request.get(
    `/api/documents/${cv.document_id}/versions/${cv.number}/pdf`,
  );
  expect(pdf.status()).toBe(200);
  const bytes = await pdf.body();
  expect(bytes.length).toBeGreaterThan(0);
  expect(bytes.subarray(0, 5).toString()).toBe("%PDF-");
  return bytes;
}

async function cvMatches(where: Page, app: string, submission: Submission) {
  const bytes = await pinnedCv(where, app);
  expect(submission.accepted).toBe(true);
  expect(submission.files).toHaveLength(1);
  const uploaded = submission.files[0]!;
  expect(uploaded.field).toBe("resume");
  expect(uploaded.size).toBe(bytes.length);
  expect(uploaded.sha256).toBe(
    createHash("sha256").update(bytes).digest("hex"),
  );
  expect(await stub.file(uploaded.sha256)).toEqual(bytes);
}

function noLeft(view: Approval) {
  expect(view.answers.filter((one) => one.source === "left")).toEqual([]);
  expect(view.answers.find((one) => one.label === "Summary")).toMatchObject({
    value: SUMMARY,
    source: "bank",
  });
  expect(view.answers.some((one) => one.field_key.startsWith("skipped:"))).toBe(
    false,
  );
}

test.beforeAll(async ({ browser }) => {
  person = invented("known-fallback");
  context = await newContext(browser);
  page = await context.newPage();
  errors = watchErrors(page);
});

test.afterAll(async () => {
  await context.close();
  clearAttempts([person.email]);
});

test("a person explicitly saves the grounded Summary and prepares the native fallback", async () => {
  await stub.reset();
  await prepare(page, person);
  expect(
    (
      await page.request.post("/api/apply/answer-bank", {
        headers: CSRF,
        data: { question: "Summary", answer: SUMMARY },
      })
    ).status(),
  ).toBe(201);
  const cap = (await (await page.request.get("/api/apply/caps")).json()) as {
    revision: string;
  };
  expect(
    (
      await page.request.put("/api/apply/caps", {
        headers: CSRF,
        data: {
          revision: cap.revision,
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
});

test("a changed hosted Ashby form uses the default worker and submits only after person approval", async ({
  browser,
}) => {
  const reviewPerson = invented("known-fallback-review");
  const reviewContext = await newContext(browser);
  const reviewPage = await reviewContext.newPage();
  const reviewErrors = watchErrors(reviewPage);
  try {
    await prepare(reviewPage, reviewPerson);
    expect(
      (
        await reviewPage.request.post("/api/apply/answer-bank", {
          headers: CSRF,
          data: { question: "Summary", answer: SUMMARY },
        })
      ).status(),
    ).toBe(201);

    const job = await hosted(reviewPage, reviewPerson);
    await start(reviewPage, job.id);
    await expect
      .poll(async () => (await latest(reviewPage, job.id)).state, {
        timeout: FILL,
      })
      .toBe("ready");
    const app = await latest(reviewPage, job.id);
    const view = await approval(reviewPage, app.id);
    expect(app).toMatchObject({ mode: "review", ats: "ashby" });
    expect(audit(app.id).strategy).toMatchObject({ vendor: "ashby" });
    noLeft(view);
    expect(await sent()).toEqual([]);
    await reviewPage.goto(`/jobs/${job.id}`);
    const section = reviewPage.getByRole("region", { name: "Apply" });
    await shots(reviewPage, "hosted-review");
    await section.getByRole("button", { name: "Approve and submit" }).click();
    await reviewPage
      .getByRole("alertdialog")
      .getByRole("button", { name: "Approve and submit" })
      .click();
    await expect(section).toContainText("Applied.", { timeout: FILL });
    const submissions = await sent();
    expect(submissions).toHaveLength(1);
    expect(submissions[0]!.fields.email).toBe(reviewPerson.email);
    await cvMatches(reviewPage, app.id, submissions[0]!);
    await shots(reviewPage, "hosted-applied");

    expect(reviewErrors).toEqual([]);
  } finally {
    await reviewContext.close();
    clearAttempts([reviewPerson.email]);
  }
});

test("three real Full auto rehearsals of the hosted fallback send nothing", async () => {
  expect(
    (
      await page.request.put("/api/apply/settings", {
        headers: CSRF,
        data: { mode_after_like: "full_auto" },
      })
    ).status(),
  ).toBe(200);
  // The catalogue has one canonical Ashby posting. Reuse it after each person cancellation.
  const job = (fullAutoJob = await hosted(page, person));
  const rehearsals = new Set<string>();
  for (let index = 0; index < 3; index += 1) {
    expect(
      (
        await page.request.post(`/api/postings/${job.id}/like`, {
          headers: CSRF,
        })
      ).status(),
    ).toBe(201);
    await expect
      .poll(async () => (await latest(page, job.id)).state, { timeout: FILL })
      .toBe("rehearsed");
    const app = await latest(page, job.id);
    expect(rehearsals.has(app.id)).toBe(false);
    rehearsals.add(app.id);
    noLeft(await approval(page, app.id));
    expect(await sent()).toHaveLength(1);
    expect(
      (
        await page.request.post(`/api/applications/${app.id}/rehearsal`, {
          headers: CSRF,
          data: { answer: "yes" },
        })
      ).status(),
    ).toBe(200);
    expect(await latest(page, job.id)).toMatchObject({
      mode: "review",
      state: "ready",
    });
    // Yes makes this a live Review item. The person cancels it before the next Like.
    expect(
      (
        await page.request.post(`/api/applications/${app.id}/cancel`, {
          headers: CSRF,
        })
      ).status(),
    ).toBe(200);
    expect(await latest(page, job.id)).toMatchObject({ state: "cancelled" });
  }
  expect(rehearsals.size).toBe(3);
  expect(
    await (await page.request.get("/api/apply/settings")).json(),
  ).toMatchObject({ rehearsals: 3, rehearsals_done: 3 });
});

test("eligible hosted fallback waits for its hold and submits exactly once", async () => {
  const job = fullAutoJob;
  expect(
    (
      await page.request.post(`/api/postings/${job.id}/like`, { headers: CSRF })
    ).status(),
  ).toBe(201);
  await expect
    .poll(async () => (await latest(page, job.id)).state, { timeout: FILL })
    .toBe("held");
  const app = await latest(page, job.id);
  expect(app).toMatchObject({ mode: "full_auto", ats: "ashby" });
  noLeft(await approval(page, app.id));
  expect(await sent()).toHaveLength(1);
  await page.goto("/applications");
  await expect(
    page
      .getByRole("article", { name: job.title, exact: true })
      .getByRole("timer"),
  ).toBeVisible();
  await shots(page, "hosted-held");
  await expect
    .poll(async () => (await latest(page, job.id)).state, { timeout: FILL })
    .toBe("submitted");
  const submissions = await sent();
  expect(submissions).toHaveLength(2);
  await cvMatches(page, app.id, submissions[1]!);
  const detail = (await (
    await page.request.get(`/api/applications/${app.id}`)
  ).json()) as {
    events: Array<{ kind: string; at: string; data: Record<string, unknown> }>;
  };
  const held = detail.events.find(
    (one) => one.kind === "decision" && one.data.outcome === "held",
  );
  const clicks = detail.events.filter((one) => one.kind === "click");
  expect(held).toBeDefined();
  expect(clicks).toHaveLength(1);
  expect(Date.parse(clicks[0]!.at)).toBeGreaterThanOrEqual(
    Date.parse(String(held!.data.until)),
  );
  expect(errors).toEqual([]);
});

for (const framed of [true, false]) {
  test(
    framed
      ? "a known framed mid-fill change stays in its frame and hands Full auto to Review"
      : "without a credential a generic native form prefills sources and leaves the unknown",
    async ({ browser }) => {
      const who = invented(framed ? "framed-fallback" : "no-credential");
      const own = await newContext(browser);
      const where = await own.newPage();
      const faults = watchErrors(where);
      try {
        await prepare(where, who);
        const job = await posting(
          where,
          who,
          `${STACK_FORMS}/forms/apply-fallback/${framed ? "framed" : "no-credential"}.html`,
        );
        expect(
          (
            await where.request.delete("/api/me/model-credential", {
              headers: CSRF,
            })
          ).status(),
        ).toBe(204);
        const runList = (await (
          await where.request.get("/api/agents/runs")
        ).json()) as { items: Array<{ id: string }> };
        const runs = runList.items.map((one) => one.id);
        if (framed) {
          expect(
            (
              await where.request.put("/api/apply/settings", {
                headers: CSRF,
                data: { mode_after_like: "full_auto" },
              })
            ).status(),
          ).toBe(200);
          expect(
            (
              await where.request.post(`/api/postings/${job.id}/like`, {
                headers: CSRF,
              })
            ).status(),
          ).toBe(201);
        } else await start(where, job.id);
        await expect
          .poll(async () => (await latest(where, job.id)).state, {
            timeout: FILL,
          })
          .toBe("needs_person");
        const app = await latest(where, job.id);
        expect(app.mode).toBe("review");
        const view = await approval(where, app.id);
        for (const [label, value, source] of [
          ["First name", "Morgan", "identity"],
          [framed ? "Family name" : "Last name", "Exampleton", "identity"],
          ["Phone", "+33 6 00 00 00 00", "identity"],
          ["How did you hear about us?", "Company website", "answers"],
        ] as const) {
          expect(
            view.answers.find((one) => one.label.startsWith(label)),
          ).toMatchObject({ value, source });
        }
        expect(
          view.answers.find((one) => one.label.startsWith("Email")),
        ).toMatchObject({ value: who.email, source: "identity" });
        expect(
          view.answers.find((one) => one.label.startsWith("Resume")),
        ).toMatchObject({ source: "document" });
        expect(
          view.answers.find((one) => one.label === "Summary"),
        ).toMatchObject({ source: "left" });
        expect(
          view.answers.some((one) => one.field_key === "designated:submit"),
        ).toBe(false);
        const after = (await (
          await where.request.get("/api/agents/runs")
        ).json()) as { items: Array<{ id: string }> };
        expect(after.items.map((one) => one.id)).toEqual(runs);
        const detail = (await (
          await where.request.get(`/api/applications/${app.id}`)
        ).json()) as {
          events: Array<{ kind: string; data: Record<string, unknown> }>;
        };
        expect(
          detail.events.filter((one) => one.kind === "filled").at(-1)?.data,
        ).toEqual({
          filled: 6,
          unverified: 0,
          failed: 0,
          left: 1,
          agent: 0,
          skipped: 0,
        });
        await pinnedCv(where, app.id);
        expect(detail.events.some((one) => one.kind === "click")).toBe(false);
        expect(audit(app.id).cost).toMatchObject({
          input_tokens: 0,
          output_tokens: 0,
          stopped: "no_credential",
        });
        if (framed) {
          expect(audit(app.id).strategy).toMatchObject({
            vendor: "greenhouse",
            frame: 0,
          });
          expect(
            view.answers.find((one) => one.label.startsWith("Family name")),
          ).toMatchObject({ value: "Exampleton", source: "identity" });
          expect(JSON.stringify(detail.events)).toContain(
            "frame_origin_unverified",
          );
        }
        expect(
          (await stub.submissions()).filter(
            (one) =>
              one.org === `custom-${framed ? "mid-fill" : "no-credential"}`,
          ),
        ).toEqual([]);
        await where.goto(`/jobs/${job.id}`);
        await shots(where, framed ? "framed-review" : "no-credential-review");
        expect(faults).toEqual([]);
      } finally {
        await own.close();
        clearAttempts([who.email]);
      }
    },
  );
}
