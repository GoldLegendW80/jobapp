import { execFileSync } from "node:child_process";

import { expect, test, type BrowserContext, type Page } from "@playwright/test";

import {
  addPosting,
  caddyUrl,
  invented,
  keep,
  patchDocument,
  pdfOf,
  saveDraft,
  seedDocuments,
  signUp,
  waitForRender,
  type Person,
} from "./documents-helpers";
import { clearAttempts, newContext, root, watchErrors } from "./stack";

/**
 * Documents D20 against the real stack through Caddy, the worker and the real renderer, through
 * the api only (no writer runs): an invented person's posting CV and letter, written by hand,
 * are switched to each of templates 9 to 12 (`accent`, `executive`, `grid`, `mono`); each switch
 * makes a new version whose PDF is one page and holds the invented name. The first page of each
 * PDF is kept as evidence (tasks/evidence/documents-d20/).
 *
 * Locally, as documents-d6.spec.ts, then:
 *   E2E_CADDY_URL=http://127.0.0.1:8099 E2E_COMPOSE_PROJECT=jobapp-d20 \
 *     pnpm --dir web exec playwright test -c e2e/documents-all.config.ts documents-d20.spec.ts
 */
test.describe.configure({ mode: "serial" });
test.skip(!caddyUrl, "set E2E_CADDY_URL with the Compose stack running");

const EVIDENCE = "documents-d20";
const TEMPLATES = ["accent", "executive", "grid", "mono"];

const POSTING_LETTER = `
import json, sys, uuid
from app.config import get_settings
from app.db import build_engine, build_sessionmaker
from app.documents import store
from app.documents.models import Kind
from app.identity import service as identity
email, posting = sys.argv[1], uuid.UUID(sys.argv[2])
if not (email.startswith("e2e-") and email.endswith("@example.org")):
    raise SystemExit("refusing a non e2e account")
engine = build_engine(get_settings())
try:
    with build_sessionmaker(engine)() as db, db.begin():
        user = identity.get_user_by_email(db, email)
        letter = store.get_or_create(db, user.id, posting, Kind.letter, at=identity.utcnow())
        out = {"letter": str(letter.id)}
finally:
    engine.dispose()
print(json.dumps(out))
`;

/** The letter of a posting, made in the api container as the letter writer makes it. */
function postingLetter(person: Person, postingId: string): string {
  if (!/^e2e-[a-z0-9.-]+@example\.org$/.test(person.email))
    throw new Error(`refusing to seed documents for ${person.email}`);
  if (!/^[0-9a-f-]{36}$/.test(postingId)) throw new Error("not a posting id");
  const out = execFileSync(
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
      POSTING_LETTER,
      person.email,
      postingId,
    ],
    { cwd: root, encoding: "utf8" },
  );
  return (JSON.parse(out.trim().split("\n").pop() ?? "") as { letter: string })
    .letter;
}

const LETTER_DRAFT = {
  schema: 1,
  target: "Platform Engineer at Brindle Example Labs",
  headline: "Platform Engineer",
  recipient: "Hiring team, Brindle Example Labs",
  subject: "Calm releases for your platform team",
  salutation: "Dear hiring team,",
  paragraphs: [
    "I run the deployment platform of Quillmere Example Works, the path every product team takes from the first commit to production.",
    "Before that, at Tarnwick Sample Studio, I built internal tools for the support team and learned to treat slow feedback as a defect.",
    "I would be glad to talk about your platform and what it needs next.",
  ],
  closing: "Kind regards,",
};

let person: Person;
let context: BrowserContext;
let page: Page;
let errors: string[];
const ids: Record<"cv" | "letter", string> = { cv: "", letter: "" };
let latest: Record<"cv" | "letter", number> = { cv: 0, letter: 0 };

test.beforeAll(async ({ browser }) => {
  person = invented("d20");
  context = await newContext(browser);
  page = await context.newPage();
  errors = watchErrors(page);
});

test.afterAll(async () => {
  await context.close();
  clearAttempts([person.email]);
});

test("an invented person's posting CV and letter, written by hand, render", async () => {
  await signUp(page, person);
  const postingId = await addPosting(page.request, person);
  ids.cv = seedDocuments(person, postingId).cv;
  ids.letter = postingLetter(person, postingId);

  await saveDraft(page.request, ids.cv, person.cvDraft);
  await saveDraft(page.request, ids.letter, LETTER_DRAFT);
  latest = {
    cv: (await waitForRender(page.request, ids.cv, 1)).latest?.number ?? 0,
    letter:
      (await waitForRender(page.request, ids.letter, 1)).latest?.number ?? 0,
  };
});

for (const slug of TEMPLATES) {
  test(`the CV and the letter switched to ${slug} each print one page`, async () => {
    for (const kind of ["cv", "letter"] as const) {
      const patched = await patchDocument(page.request, ids[kind], {
        template_slug: slug,
      });
      expect(patched.template_slug).toBe(slug);
      const rendered = await waitForRender(
        page.request,
        ids[kind],
        latest[kind] + 1,
      );
      const version = rendered.latest;
      expect(version).toEqual(
        expect.objectContaining({
          template_slug: slug,
          pages: 1,
          has_pdf: true,
        }),
      );
      latest[kind] = version?.number ?? 0;
      const pdf = await pdfOf(page.request, ids[kind], latest[kind]);
      expect(pdf.pages).toBe(1);
      expect(pdf.text).toContain(person.name);
      if (kind === "cv") expect(pdf.text).toContain(person.firstEmployer);
      const image = await page.request.get(
        `/api/documents/${ids[kind]}/versions/${latest[kind]}/page1.png`,
      );
      expect(image.status()).toBe(200);
      keep(
        EVIDENCE,
        `${slug}-${kind}-page1.png`,
        Buffer.from(await image.body()),
      );
    }
  });
}

test("no page error on the way", () => {
  expect(errors).toEqual([]);
});
