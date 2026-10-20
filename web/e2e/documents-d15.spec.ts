import { execFileSync } from "node:child_process";
import { createHash } from "node:crypto";

import {
  expect,
  test,
  type APIRequestContext,
  type BrowserContext,
  type Page,
} from "@playwright/test";

import {
  CSRF,
  caddyUrl,
  invented,
  keep,
  readDocument,
  saveDraft,
  signUp,
  waitForRender,
  type DocumentOut,
  type Person,
} from "./documents-helpers";
import { clearAttempts, newContext, root, watchErrors } from "./stack";

/**
 * Documents D15 against the real stack through Caddy, the worker and the real renderer, one
 * fresh account and only invented data: the account's postings are loaded through the jobs
 * import api with invented legacy files (a posting `applied` with its cv.json, letter.json,
 * cv.pdf and letter.pdf; a `found` posting with its cv.json only), then the operator command
 * `python -m app.documents.cli import-legacy <email>` runs in the api container, a dry run
 * first. After it the applied posting's CV and letter are frozen with `legacy_applied` and
 * serve the legacy PDFs byte for byte, and the found posting's CV is an editable draft that
 * renders. A second run changes nothing.
 *
 * Locally, on a scratch Compose project (its own volumes, torn down afterwards):
 *   HTTP_PORT=8099 docker compose -p jobapp-d15 -f compose.yml -f compose.mac.yml up -d --wait
 *   docker compose -p jobapp-d15 -f compose.yml exec -T api alembic upgrade head
 *   E2E_CADDY_URL=http://127.0.0.1:8099 E2E_COMPOSE_PROJECT=jobapp-d15 \
 *     pnpm --dir web exec playwright test -c e2e/documents-all.config.ts documents-d15.spec.ts
 *   docker compose -p jobapp-d15 -f compose.yml down -v
 */
test.describe.configure({ mode: "serial" });
test.skip(!caddyUrl, "set E2E_CADDY_URL with the Compose stack running");

type Version = {
  number: number;
  origin: string;
  frozen_at: string | null;
  frozen_reason: string | null;
  has_pdf: boolean;
  has_preview: boolean;
};

type PostingDocuments = {
  documents: Array<DocumentOut & { sent: number[] }>;
};

const APPLIED_SLUG = "e2e-legacy-applied";
const FOUND_SLUG = "e2e-legacy-found";

let person: Person;
let context: BrowserContext;
let page: Page;
let errors: string[];
const pdfs = {
  cv: inventedPdf("cv"),
  letter: inventedPdf("letter"),
};
let appliedId: string;
let foundId: string;

/** An invented PDF: what the command line once rendered, as bytes the import must keep. */
function inventedPdf(tag: string): Buffer {
  const run = Math.random().toString(36).slice(2, 10);
  return Buffer.from(
    `%PDF-1.4\n% invented legacy ${tag} ${run}\n1 0 obj << /Type /Catalog >> endobj\n` +
      "trailer << /Root 1 0 R >>\n%%EOF\n",
    "latin1",
  );
}

function file(name: string, bytes: Buffer, mime: string) {
  return {
    name,
    mime,
    size: bytes.length,
    sha256: createHash("sha256").update(bytes).digest("hex"),
    base64: bytes.toString("base64"),
  };
}

const json = (data: unknown) => Buffer.from(JSON.stringify(data), "utf8");

/** The legacy cv.json: the employer keys of the invented profile, a role for each. */
function legacyCv(who: Person) {
  return {
    target: `Platform engineer at ${who.posting.company}`,
    headline: "Platform engineer who makes releases calm",
    summary:
      "Platform engineer who runs the deployment platform of a billing team and writes its services in Python.",
    highlights: ["Cut release preparation from days to an hour"],
    competencies: ["CI and CD", "Python", "PostgreSQL"],
    experience: [
      {
        employer_key: "quillmere",
        role: "Senior Platform Engineer",
        bullets: [
          "Run the deployment platform of the billing team, from the first commit to production.",
        ],
      },
      {
        employer_key: "tarnwick",
        role: "Developer",
        bullets: ["Wrote the studio's booking services."],
      },
    ],
    skills: [{ label: "Languages", items: ["Python", "Go"] }],
  };
}

function legacyLetter(who: Person) {
  return {
    target: `Platform engineer at ${who.posting.company}`,
    headline: "Platform engineer",
    recipient: `Hiring team, ${who.posting.company}`,
    subject: "Calm releases for your platform team",
    salutation: "Dear hiring team,",
    paragraphs: [
      "Your posting asks for someone who can make releases boring and incidents rare.",
      "I run the deployment platform of a billing team and wrote its services in Python.",
    ],
    closing: "Kind regards,",
  };
}

function importDocument(who: Person) {
  const posting = {
    company: who.posting.company,
    title: who.posting.title,
    location: who.posting.location,
    posting_text: who.posting.posting_text,
    fit: 90,
  };
  return {
    format: "jobapp-jobs",
    version: 1,
    exported_at: new Date().toISOString(),
    jobs: [
      {
        ...posting,
        slug: APPLIED_SLUG,
        status: "applied",
        applied_on: "2026-09-01",
        files: [
          file("cv.json", json(legacyCv(who)), "application/json"),
          file("letter.json", json(legacyLetter(who)), "application/json"),
          file("cv.pdf", pdfs.cv, "application/pdf"),
          file("letter.pdf", pdfs.letter, "application/pdf"),
        ],
      },
      {
        ...posting,
        slug: FOUND_SLUG,
        status: "found",
        files: [file("cv.json", json(legacyCv(who)), "application/json")],
      },
    ],
    warnings: [],
  };
}

/** `python -m app.documents.cli import-legacy` in the api container of the Compose project. */
function importLegacy(email: string, dryRun = false): string {
  if (!/^e2e-[a-z0-9.-]+@example\.org$/.test(email))
    throw new Error(`refusing to import for ${email}`);
  return execFileSync(
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
      "-m",
      "app.documents.cli",
      "import-legacy",
      email,
      ...(dryRun ? ["--dry-run"] : []),
    ],
    { cwd: root, encoding: "utf8" },
  );
}

async function postingId(request: APIRequestContext, slug: string) {
  const response = await request.get("/api/jobs?limit=200");
  expect(response.status()).toBe(200);
  const rows = (
    (await response.json()) as {
      items: Array<{ id: string; slug: string }>;
    }
  ).items;
  const found = rows.find((row) => row.slug === slug);
  if (!found) throw new Error(`no posting ${slug}`);
  return found.id;
}

async function documentsOf(request: APIRequestContext, id: string) {
  const response = await request.get(`/api/jobs/${id}/documents`);
  expect(response.status()).toBe(200);
  return ((await response.json()) as PostingDocuments).documents;
}

async function versions(request: APIRequestContext, documentId: string) {
  const response = await request.get(`/api/documents/${documentId}/versions`);
  expect(response.status()).toBe(200);
  return (await response.json()) as Version[];
}

async function rawPdf(
  request: APIRequestContext,
  documentId: string,
  number: number,
): Promise<Buffer> {
  const response = await request.get(
    `/api/documents/${documentId}/versions/${number}/pdf`,
  );
  expect(response.status()).toBe(200);
  expect(response.headers()["content-type"]).toBe("application/pdf");
  return Buffer.from(await response.body());
}

test.beforeAll(async ({ browser }) => {
  person = invented("d15");
  context = await newContext(browser);
  page = await context.newPage();
  errors = watchErrors(page);
});

test.afterAll(async () => {
  await context.close();
  clearAttempts([person.email]);
});

test("the postings arrive with their legacy files and no documents yet", async () => {
  await signUp(page, person);
  const imported = await page.request.post("/api/jobs/import", {
    headers: CSRF,
    data: importDocument(person),
  });
  expect(imported.ok(), await imported.text()).toBe(true);
  appliedId = await postingId(page.request, APPLIED_SLUG);
  foundId = await postingId(page.request, FOUND_SLUG);
  expect(await documentsOf(page.request, appliedId)).toEqual([]);
  expect(await documentsOf(page.request, foundId)).toEqual([]);

  const dry = importLegacy(person.email, true);
  expect(dry).toContain(
    "Dry run, nothing was written. Would import 2 CVs and 1 letters from 2 postings",
  );
  expect(dry).not.toContain(person.email);
  expect(await documentsOf(page.request, appliedId)).toEqual([]);
});

test("the applied posting's CV and letter are frozen and serve the legacy PDFs", async () => {
  const done = importLegacy(person.email);
  expect(done).toContain("Imported 2 CVs and 1 letters from 2 postings");
  expect(done).toContain(
    "Versions: 3, of which 2 frozen as sent with their PDF.",
  );
  expect(done).toContain("Roles moved to notes: 4.");

  const documents = await documentsOf(page.request, appliedId);
  expect(documents.map((d) => d.kind).sort()).toEqual(["cv", "letter"]);
  for (const document of documents) {
    expect(document.template_slug).toBe("classic");
    expect(document.sent).toEqual([1]);
    const [only] = await versions(page.request, document.id);
    expect(only).toMatchObject({
      number: 1,
      origin: "legacy",
      frozen_reason: "legacy_applied",
      has_pdf: true,
      has_preview: false,
    });
    // The command line's PDF never reaches PDFium in the api: no page image of it.
    const image = await page.request.get(
      `/api/documents/${document.id}/versions/1/page1.png`,
    );
    expect(image.status()).toBe(404);
    const served = await rawPdf(page.request, document.id, 1);
    expect(served.equals(pdfs[document.kind])).toBe(true);
    keep("documents-d15", `legacy-${document.kind}.pdf`, served);
  }
  const cv = documents.find((d) => d.kind === "cv");
  expect(cv?.draft.notes).toBe(
    "Role in the imported CV for quillmere: Senior Platform Engineer\n" +
      "Role in the imported CV for tarnwick: Developer",
  );
});

test("the found posting's CV is an editable draft that renders", async () => {
  const all = await documentsOf(page.request, foundId);
  expect(all.map((d) => d.kind)).toEqual(["cv"]);
  const cv = all[0];
  if (!cv) throw new Error("no CV imported for the found posting");
  expect(cv.latest).toMatchObject({ number: 1, has_pdf: false });
  const listed = await versions(page.request, cv.id);
  expect(listed.map((v) => [v.origin, v.frozen_reason])).toEqual([
    ["legacy", null],
  ]);

  const asked = await page.request.post(`/api/documents/${cv.id}/render`, {
    headers: CSRF,
  });
  expect(asked.status(), await asked.text()).toBe(202);
  const rendered = await waitForRender(page.request, cv.id, 1);
  expect(rendered.latest?.number).toBe(1);

  const draft = (await readDocument(page.request, cv.id)).draft;
  await saveDraft(page.request, cv.id, {
    ...draft,
    headline: "Platform engineer, edited after the import",
  });
  const edited = await waitForRender(page.request, cv.id, 2);
  expect(edited.latest?.number).toBe(2);
});

test("a second run changes nothing", async () => {
  const sent = await documentsOf(page.request, appliedId);
  const before = await Promise.all(
    sent.map((document) => versions(page.request, document.id)),
  );
  const again = importLegacy(person.email);
  expect(again).toContain("Imported 0 CVs and 0 letters from 2 postings");
  expect(again).toContain("Already there, left as they are: 3.");
  const after = await Promise.all(
    sent.map((document) => versions(page.request, document.id)),
  );
  expect(after).toEqual(before);
  for (const document of sent) {
    const served = await rawPdf(page.request, document.id, 1);
    expect(served.equals(pdfs[document.kind])).toBe(true);
  }
  expect(errors).toEqual([]);
});
