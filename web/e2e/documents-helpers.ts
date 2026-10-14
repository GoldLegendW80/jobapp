import { execFileSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";

import { expect, type APIRequestContext, type Page } from "@playwright/test";

import {
  caddyUrl,
  cliImportProfile,
  cliLink,
  root,
  setPassword,
} from "./stack";

/**
 * Helpers of the documents journeys (task D6). A later task never edits this file: it adds the
 * helpers it needs in its own spec file.
 *
 * Only invented data goes in: an `e2e-*@example.org` account, a made up profile, base CV and
 * posting. The account is made with the operator CLI, the profile imported with
 * `app.profile.cli import`; a base CV and the posting's empty CV have no route before D9 and
 * D7, so `seedDocuments` makes them in the api container with `app.documents.store`, the code
 * the writers will call. Everything else goes through the api as the page will: save a draft
 * with `If-Match`, ask for a render, poll the document until its render is stored.
 *
 * - `invented(tag)`: a fresh person, their profile export, a posting and a CV draft.
 * - `signUp(page, person)`: the account, its password and its profile.
 * - `addPosting`, `seedDocuments`, `readDocument`, `saveDraft`, `patchDocument`,
 *   `waitForRender`, `jobStatus`: the api calls.
 * - `pdfOf`: a version's PDF with its page count and text, read with pypdfium2 in the api
 *   container (the image has it for `page1.png`).
 * - `keep`: writes a file under tasks/evidence/<E2E_EVIDENCE or the default>.
 */

export const CSRF = { "X-Requested-With": "jobapp" };

export type Render = {
  state: "idle" | "queued" | "rendering";
  revision: number | null;
  error: string | null;
  message: string | null;
};

export type VersionSummary = {
  number: number;
  template_slug: string;
  pages: number | null;
  fit: string | null;
  has_pdf: boolean;
};

export type DocumentOut = {
  id: string;
  kind: "cv" | "letter";
  posting_id: string | null;
  template_slug: string;
  draft: Record<string, unknown>;
  draft_revision: number;
  notes: Array<Record<string, unknown>>;
  render: Render;
  latest: VersionSummary | null;
};

export type Person = {
  email: string;
  name: string;
  password: string;
  firstEmployer: string;
  company: string;
  profile: unknown;
  posting: {
    company: string;
    title: string;
    location: string;
    posting_text: string;
  };
  baseCv: Record<string, unknown>;
  cvDraft: Record<string, unknown>;
};

const COMPOSE = () => [
  "compose",
  "-p",
  process.env.E2E_COMPOSE_PROJECT ?? "jobapp",
  "-f",
  "compose.yml",
];

/** Run Python in the api container of the local Compose project; stdin is `input`. */
function apiPython(
  code: string,
  args: string[],
  input?: string | Buffer,
): string {
  return execFileSync(
    "docker",
    [...COMPOSE(), "exec", "-T", "api", "python", "-c", code, ...args],
    { cwd: root, encoding: "utf8", input, maxBuffer: 16 * 1024 * 1024 },
  );
}

const terms = (list: string[]) => ({ advanced_regex: false, terms: list });

/** A fresh invented person: profile export, posting, base CV and a hand written CV draft. */
export function invented(tag: string): Person {
  const run = `${Date.now().toString(36)}${Math.random().toString(36).slice(2, 6)}`;
  const email = `e2e-documents-${tag}-${run}@example.org`;
  const name = "Morgan Exampleton";
  const firstEmployer = "Quillmere Example Works";
  const company = "Brindle Example Labs";
  const employers = [
    {
      key: "quillmere",
      organisation: firstEmployer,
      title: "Platform Engineer",
      start: "2023-02",
      end: null,
      location: "Nantes",
      contract: "CDI",
    },
    {
      key: "tarnwick",
      organisation: "Tarnwick Sample Studio",
      title: "Software Developer",
      start: "2021-01",
      end: "2023-01",
      location: "Rennes",
      contract: "CDI",
    },
  ];
  const experience = [
    {
      employer_key: "quillmere",
      bullets: [
        "Run the deployment platform of the billing team, from the first commit to production.",
        "Wrote the platform's backend services in Python with FastAPI and PostgreSQL.",
      ],
    },
    {
      employer_key: "tarnwick",
      bullets: [
        "Built internal tools in Python and React for the support team.",
      ],
    },
  ];
  const skills = [
    { label: "Cloud", items: ["Kubernetes", "Terraform"] },
    { label: "Backend", items: ["Python", "FastAPI", "PostgreSQL"] },
    { label: "Frontend", items: ["React", "TypeScript"] },
    { label: "Languages", items: ["English, fluent", "French, native"] },
  ];
  const competencies = [
    "Kubernetes",
    "Terraform",
    "Python",
    "FastAPI",
    "PostgreSQL",
    "CI/CD",
    "Platform engineering",
    "React",
  ];
  return {
    email,
    name,
    password: `documents ${tag} journey ${run} passphrase`,
    firstEmployer,
    company,
    profile: {
      format: "jobapp-profile",
      version: 1,
      exported_at: "2026-10-14T08:00:00Z",
      sections: {
        identity: {
          full_name: name,
          headline: "Platform Engineer",
          email,
          phone: "+33 6 00 00 00 00",
          city: "Nantes",
          country: "France",
          linkedin_url: null,
          website: null,
          links: [],
          languages: [
            { name: "English", level: "Fluent" },
            { name: "French", level: "Native" },
          ],
        },
        experience: {
          employers,
          education: [
            {
              school: "Example Institute of Technology",
              degree: "BSc Computer Science",
              start: "2017",
              end: "2020",
              location: "Nantes",
              details: null,
            },
          ],
        },
        facts: {
          blocks: [
            {
              title: "Experience",
              body: "About 3.5 years of professional engineering.\nAt Quillmere Example Works I run the deployment platform of the billing team.",
            },
          ],
        },
        skills: {
          years_experience: 3.5,
          have: [
            { name: "cloud", ...terms(["kubernetes", "terraform"]) },
            { name: "python", ...terms(["python", "fastapi"]) },
          ],
          adjacent: [{ name: "frontend", ...terms(["react", "typescript"]) }],
          none: [{ name: "mobile", ...terms(["swift", "kotlin"]) }],
          gates: [],
        },
        search: {
          title_include: terms(["platform engineer"]),
          title_exclude: terms(["intern"]),
          location: terms(["Nantes", "Remote"]),
          contract_types: ["CDI"],
          remote_stance: "hybrid",
          min_fit: 70,
          days: 30,
          boards: [],
          never_apply: [],
        },
        answers: {
          work_authorization: [
            {
              country: "France",
              answer: "EU citizen, no sponsorship needed.",
              needs_sponsorship: false,
            },
          ],
          notice_period: "One month.",
          earliest_start: "January 2027",
          salary_policy: "Against the band.",
          relocation: null,
          travel: null,
          motivation: "I build platforms that other engineers rely on.",
          heard_about: null,
          leave_blank: [],
        },
      },
    },
    posting: {
      company,
      title: "Senior Platform Engineer",
      location: "Nantes, France",
      posting_text:
        "Brindle Example Labs is hiring a Senior Platform Engineer.\n\nYou will run our deployment platform on Kubernetes and write backend services in Python.",
    },
    baseCv: {
      schema: 1,
      target: "General",
      headline: "Platform Engineer, cloud and backend",
      summary:
        "Platform engineer with about 3.5 years of professional engineering. I run the deployment platform of the billing team at Quillmere Example Works.",
      highlights: [
        "Deployment platform of the billing team",
        "Backend services in Python",
        "Internal tools in Python and React",
      ],
      competencies: competencies.slice(0, 4),
      experience,
      skills,
      notes: "",
    },
    cvDraft: {
      schema: 1,
      target: `${company}, Senior Platform Engineer`,
      headline: "Platform Engineer for teams that ship every day",
      summary:
        "Platform engineer with about 3.5 years of professional engineering. At Quillmere Example Works I run the deployment platform of the billing team.",
      highlights: [
        "Deployment platform of the billing team",
        "Backend services in Python",
        "Internal tools in Python and React",
      ],
      competencies,
      experience,
      skills,
    },
  };
}

/** The account (operator CLI link, then its password), signed in on `page`, and its profile. */
export async function signUp(page: Page, person: Person) {
  await page.goto(cliLink(person.email));
  await setPassword(page, person.password, person.name);
  cliImportProfile(person.email, person.profile);
}

/** The posting, added by hand through the api; its id. */
export async function addPosting(
  request: APIRequestContext,
  person: Person,
): Promise<string> {
  const created = await request.post("/api/jobs", {
    headers: CSRF,
    data: person.posting,
  });
  expect(created.status()).toBe(201);
  return ((await created.json()) as { id: string }).id;
}

const SEED = `
import json, sys, uuid
from app.config import get_settings
from app.db import build_engine, build_sessionmaker
from app.documents import store
from app.documents.models import Author, BaseSource, Kind
from app.identity import service as identity
email, posting = sys.argv[1], uuid.UUID(sys.argv[2])
if not (email.startswith("e2e-") and email.endswith("@example.org")):
    raise SystemExit("refusing a non e2e account")
base = json.load(sys.stdin)
engine = build_engine(get_settings())
try:
    with build_sessionmaker(engine)() as db, db.begin():
        user = identity.get_user_by_email(db, email)
        at = identity.utcnow()
        made = store.create_base(
            db, user.id, "Invented base CV", BaseSource.built, at=at, content=base,
            written_by=Author.user,
        )
        cv = store.get_or_create(db, user.id, posting, Kind.cv, at=at)
        out = {"base": str(made.id), "cv": str(cv.id)}
finally:
    engine.dispose()
print(json.dumps(out))
`;

/** The person's default base CV and the posting's empty CV, made in the api container. */
export function seedDocuments(
  person: Person,
  postingId: string,
): { base: string; cv: string } {
  if (!/^e2e-[a-z0-9.-]+@example\.org$/.test(person.email))
    throw new Error(`refusing to seed documents for ${person.email}`);
  if (!/^[0-9a-f-]{36}$/.test(postingId)) throw new Error("not a posting id");
  const out = apiPython(
    SEED,
    [person.email, postingId],
    JSON.stringify(person.baseCv),
  );
  return JSON.parse(out.trim().split("\n").pop() ?? "") as {
    base: string;
    cv: string;
  };
}

export async function readDocument(
  request: APIRequestContext,
  id: string,
): Promise<DocumentOut> {
  const response = await request.get(`/api/documents/${id}`);
  expect(response.status()).toBe(200);
  return (await response.json()) as DocumentOut;
}

/** Save the draft by hand at the document's current revision; the answer (render queued). */
export async function saveDraft(
  request: APIRequestContext,
  id: string,
  content: Record<string, unknown>,
): Promise<DocumentOut> {
  const current = await readDocument(request, id);
  const saved = await request.put(`/api/documents/${id}/draft`, {
    headers: { ...CSRF, "If-Match": String(current.draft_revision) },
    data: content,
  });
  expect(saved.status(), await saved.text()).toBe(200);
  return (await saved.json()) as DocumentOut;
}

export async function patchDocument(
  request: APIRequestContext,
  id: string,
  change: { language?: string; template_slug?: string },
): Promise<DocumentOut> {
  const patched = await request.patch(`/api/documents/${id}`, {
    headers: CSRF,
    data: change,
  });
  expect(patched.status(), await patched.text()).toBe(200);
  return (await patched.json()) as DocumentOut;
}

/**
 * Poll the document until its render settled (the state back to `idle` with a render error, or
 * with a latest version numbered at least `atLeast`), then require that version's PDF and no
 * error: a failed render fails at once with its code, not at the timeout. The worker renders
 * through the real renderer, a few seconds per page fit.
 */
export async function waitForRender(
  request: APIRequestContext,
  id: string,
  atLeast: number,
): Promise<DocumentOut> {
  let document: DocumentOut | undefined;
  await expect
    .poll(
      async () => {
        document = await readDocument(request, id);
        const { render, latest } = document;
        const settled =
          render.state === "idle" &&
          (render.error !== null || (latest?.number ?? 0) >= atLeast);
        return settled
          ? "settled"
          : `${render.state}, latest ${latest?.number ?? "none"}`;
      },
      { timeout: 150_000, intervals: [500, 1_000, 2_000] },
    )
    .toBe("settled");
  if (!document) throw new Error("no document read");
  expect(document.render.error, document.render.message ?? "").toBeNull();
  expect(document.latest?.has_pdf).toBe(true);
  return document;
}

export async function jobStatus(
  request: APIRequestContext,
  jobId: string,
): Promise<string> {
  const response = await request.get(`/api/jobs/${jobId}`);
  expect(response.status()).toBe(200);
  return ((await response.json()) as { status: string }).status;
}

const PDF_TEXT = `
import json, sys
import pypdfium2 as pdfium
pdf = pdfium.PdfDocument(sys.stdin.buffer.read())
text = "\\n".join(pdf[i].get_textpage().get_text_range() for i in range(len(pdf)))
print(json.dumps({"pages": len(pdf), "text": text}))
`;

/** A version's PDF as the api serves it, with its file name, page count and text. */
export async function pdfOf(
  request: APIRequestContext,
  id: string,
  number: number,
): Promise<{ bytes: Buffer; filename: string; pages: number; text: string }> {
  const response = await request.get(
    `/api/documents/${id}/versions/${number}/pdf`,
  );
  expect(response.status()).toBe(200);
  expect(response.headers()["content-type"]).toBe("application/pdf");
  const bytes = Buffer.from(await response.body());
  const disposition = response.headers()["content-disposition"] ?? "";
  const filename = /filename="([^"]+)"/.exec(disposition)?.[1] ?? "";
  const read = JSON.parse(apiPython(PDF_TEXT, [], bytes)) as {
    pages: number;
    text: string;
  };
  return { bytes, filename, ...read };
}

/** Write a file of evidence under tasks/evidence/<E2E_EVIDENCE or `fallback`>. */
export function keep(fallback: string, name: string, bytes: Buffer) {
  const folder = path.join(
    root,
    "tasks/evidence",
    process.env.E2E_EVIDENCE ?? fallback,
  );
  fs.mkdirSync(folder, { recursive: true });
  fs.writeFileSync(path.join(folder, name), bytes);
}

export { caddyUrl };
