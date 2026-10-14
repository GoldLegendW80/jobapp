import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import path from "node:path";

import { expect, test, type BrowserContext, type Page } from "@playwright/test";

import {
  caddyUrl,
  clearAttempts,
  cliLink,
  newContext,
  root,
  setPassword,
  watchErrors,
} from "./stack";

/**
 * Task G12 of the ingestion module against the real stack through Caddy, no mocks, with the
 * fake backend (AGENTS_FAKE_BACKEND=true, never in production): a new user adds two documents,
 * a run reads both to `done`, and the merge turns what it stored into proposals. The proposals
 * appear per section with a citation each, one item shows two citations, one shows a conflict
 * with both sources, the derived years carry their flag, and a citation lands on its quote in
 * the document's text. Nothing reaches the profile before an accept, an accept is one revision,
 * an undo takes it back, and another user sees none of it.
 *
 * The documents are the made up texts of api/tests/ingestion/fixtures/g12_*.txt (a fictitious
 * person), uploaded byte for byte. A step replays the stream named after the first 12
 * hexadecimal digits of its document's sha256 (api/tests/ingestion/journey_streams.py):
 *   g12_first.txt   84778c995613  a name, Example Rail from March 2019
 *   g12_second.txt  896fd0469fb8  the same name, Example Rail from May 2019, Example Post
 *
 * The list in the page lives in the review step, which the task that builds the run's page
 * (G11) turns on (`REVIEW_READY`). Until then the last test is skipped, and the list itself is
 * covered by `src/features/ingestion/proposal-list.test.tsx`.
 *
 * Locally, on a scratch Compose project (its own volumes and images, torn down afterwards):
 *   .github/scripts/ci_env.sh 8112 >.env && echo AGENTS_FAKE_BACKEND=true >>.env
 *   docker compose -p jobapp-g12 -f compose.yml -f compose.mac.yml up -d --build --wait
 *   docker compose -p jobapp-g12 -f compose.yml exec -T api alembic upgrade head
 *   E2E_CADDY_URL=http://127.0.0.1:8112 E2E_COMPOSE_PROJECT=jobapp-g12 \
 *     pnpm --dir web exec playwright test -c e2e/ingestion-all.config.ts ingestion-g12
 *   docker compose -p jobapp-g12 -f compose.yml down -v
 */
test.describe.configure({ mode: "serial" });
test.skip(!caddyUrl, "set E2E_CADDY_URL with the Compose stack running");

const stamp = Date.now().toString(36);
const EMAIL = `e2e-ingestion-g12-${stamp}@example.org`;
const OTHER_EMAIL = `e2e-ingestion-g12-other-${stamp}@example.org`;
const NAME = "Sam Example";
const PASSWORD = `ingestion merge ${stamp} passphrase`;
const tail = () => Math.random().toString(36).slice(2, 12).padEnd(10, "x");
const KEY = `sk-ant-api03-e2egood${tail()}${tail()}Rn42`;

const DOCUMENTS = "/api/ingestion/documents";
const RUNS = "/api/ingestion/runs";
const CSRF = { "X-Requested-With": "jobapp" };
const FIXTURES = path.join(root, "api/tests/ingestion/fixtures");
const FIRST = "g12_first.txt";
const SECOND = "g12_second.txt";
const HASHES: Record<string, string> = {
  [FIRST]: "84778c995613",
  [SECOND]: "896fd0469fb8",
};
const RAIL = "employers:example-rail-2019";
const POST = "employers:example-post-2016";
const YEARS = "years_experience";

const bytes = (name: string) => readFileSync(path.join(FIXTURES, name));

interface Citation {
  document_id: string;
  document: { id: string; name: string | null; deleted: boolean } | null;
  excerpt: string;
  start: number;
  end: number;
}

interface Item {
  id: string;
  status: string;
  key: string;
  value: unknown;
  before: unknown;
  stale: boolean;
  stale_reason: string | null;
  locked: boolean;
  bulk: string | null;
  flags: string[];
  citations: Citation[];
  conflict: { value: { start: string }; citations: Citation[] }[];
  depends_on: string[];
  applied_revision: number | null;
}

interface Review {
  status: string;
  profile_revision: number;
  counts: { total: number; pending: number };
  documents: { id: string; name: string | null; deleted: boolean }[];
  sections: { section: string; items: Item[] }[];
}

let context: BrowserContext;
let page: Page;
let errors: string[];
const ids: Record<string, string> = {};
let runId = "";

test.beforeAll(async ({ browser }) => {
  context = await newContext(browser);
  page = await context.newPage();
  errors = watchErrors(page, []);
});

test.afterAll(async () => {
  await context.close();
  clearAttempts([EMAIL, OTHER_EMAIL]);
});

function idOf(name: string): string {
  const one = ids[name];
  if (one === undefined) throw new Error(`no document stored as ${name}`);
  return one;
}

async function review(as: BrowserContext = context): Promise<Review> {
  const response = await as.request.get(`${RUNS}/${runId}/proposals`);
  expect(response.status()).toBe(200);
  expect(response.headers()["cache-control"]).toBe("no-store");
  return (await response.json()) as Review;
}

function itemAt(body: Review, key: string): Item {
  const found = body.sections
    .flatMap((section) => section.items)
    .filter((item) => item.key === key);
  expect(found).toHaveLength(1);
  return found[0] as Item;
}

test("the fixtures are the ones the recorded streams are named after", () => {
  for (const [name, hash] of Object.entries(HASHES))
    expect(
      createHash("sha256").update(bytes(name)).digest("hex").slice(0, 12),
    ).toBe(hash);
});

test("a run on two documents ends done and counts its proposals", async () => {
  await page.goto(cliLink(EMAIL));
  await setPassword(page, PASSWORD, NAME);
  const saved = await context.request.put("/api/me/model-credential", {
    headers: CSRF,
    data: { kind: "anthropic_api_key", secret: KEY },
  });
  expect(saved.status()).toBe(200);

  await page.goto("/profile/fill?step=documents");
  await page.getByTestId("document-files").setInputFiles(
    [FIRST, SECOND].map((name) => ({
      name,
      mimeType: "text/plain",
      buffer: bytes(name),
    })),
  );
  const rows = page
    .getByRole("list", { name: "Your documents" })
    .getByRole("listitem");
  for (const name of [FIRST, SECOND])
    await expect(rows.filter({ hasText: name })).toContainText("Ready");
  const listed = await context.request.get(DOCUMENTS);
  for (const one of (
    (await listed.json()) as { items: { id: string; name: string }[] }
  ).items)
    ids[one.name] = one.id;

  const documents = [idOf(FIRST), idOf(SECOND)];
  const estimate = async () => {
    const response = await context.request.post(`${RUNS}/estimate`, {
      headers: CSRF,
      data: { document_ids: documents, reread_document_ids: [] },
    });
    expect(response.status()).toBe(200);
    return (await response.json()) as {
      consent: { version: number };
      quote_id: string;
    };
  };
  const given = await context.request.post("/api/ingestion/consent", {
    headers: CSRF,
    data: { version: (await estimate()).consent.version },
  });
  expect(given.status()).toBe(200);
  const started = await context.request.post(RUNS, {
    headers: CSRF,
    data: {
      document_ids: documents,
      reread_document_ids: [],
      quote_id: (await estimate()).quote_id,
    },
  });
  expect(started.status()).toBe(201);
  runId = ((await started.json()) as { id: string }).id;

  interface Run {
    status: string;
    partial: boolean;
    counts: { items: number; proposals: number; done: number };
  }
  let run: Run | undefined;
  await expect
    .poll(
      async () => {
        run = (await (
          await context.request.get(`${RUNS}/${runId}`)
        ).json()) as Run;
        return run.status;
      },
      {
        message: "the run ends",
        timeout: 100_000,
        intervals: [250, 500, 1_000],
      },
    )
    .toBe("done");
  expect(run?.partial).toBe(false);
  // Five items stored, four proposals: the name once, the two employers, the derived years.
  expect(run?.counts).toMatchObject({ done: 2, items: 5, proposals: 4 });
});

test("the proposals appear per section, each with its citation", async () => {
  const body = await review();
  expect(body.sections.map((section) => section.section)).toEqual([
    "identity",
    "experience",
    "skills",
  ]);
  expect(body.counts).toMatchObject({ total: 4, pending: 4 });
  expect(body.documents.map((one) => [one.name, one.deleted])).toEqual([
    [FIRST, false],
    [SECOND, false],
  ]);
  // Every citation names its document, and its offsets land on the quote in the stored text.
  const all = body.sections.flatMap((section) => section.items);
  for (const item of all) {
    const cited = [
      ...item.citations,
      ...item.conflict.flatMap((other) => other.citations),
    ];
    expect(cited.length).toBeGreaterThan(0);
    for (const citation of cited) {
      expect([FIRST, SECOND]).toContain(citation.document?.name);
      const text = await context.request.get(
        `${DOCUMENTS}/${citation.document_id}/text?offset=${String(citation.start)}&limit=20000`,
      );
      expect(text.status()).toBe(200);
      const stored = ((await text.json()) as { text: string }).text;
      expect(stored.startsWith(citation.excerpt)).toBe(true);
      expect(citation.end - citation.start).toBe(citation.excerpt.length);
    }
  }

  // One item, two citations: both documents give the same name.
  const name = itemAt(body, "full_name");
  expect(name.value).toBe("Sam Rivera");
  expect(name.citations.map((one) => one.document?.name)).toEqual([
    FIRST,
    SECOND,
  ]);
  expect(name.flags).toEqual([]);

  // One conflict, with both sources: the two documents disagree on a start date.
  const rail = itemAt(body, RAIL);
  expect(rail.flags).toEqual(["conflict"]);
  expect(rail.conflict).toHaveLength(1);
  const values = [
    { value: rail.value as { start: string }, citations: rail.citations },
    ...rail.conflict,
  ];
  expect(values.map((one) => one.value.start).sort()).toEqual([
    "2019-03",
    "2019-05",
  ]);
  expect(values.map((one) => one.citations[0]?.document?.name).sort()).toEqual([
    FIRST,
    SECOND,
  ]);

  // The derived years: counted by the merge from the one employer the documents agree on,
  // flagged, and never part of "accept all".
  const years = itemAt(body, YEARS);
  expect(years.value).toBe(2.9);
  expect(years.flags).toEqual(["derived"]);
  expect(years.depends_on).toEqual([POST]);
  expect([years.bulk, years.stale]).toEqual(["flagged", false]);
  // Nothing reached the profile: every item is pending and the profile holds none of it.
  expect(all.every((item) => item.status === "pending")).toBe(true);
  expect(all.every((item) => item.before === null)).toBe(true);
});

test("another user sees none of it", async ({ browser }) => {
  const other = await newContext(browser);
  try {
    const otherPage = await other.newPage();
    await otherPage.goto(cliLink(OTHER_EMAIL));
    await setPassword(otherPage, PASSWORD, "Other Example");
    const unknown = await other.request.get(
      `${RUNS}/00000000-0000-4000-8000-000000000000/proposals`,
    );
    expect(unknown.status()).toBe(404);
    const theirs = await other.request.get(`${RUNS}/${runId}/proposals`);
    expect(theirs.status()).toBe(404);
    expect(await theirs.json()).toEqual(await unknown.json());
    const post = itemAt(await review(), POST);
    for (const action of [
      `/api/proposals/${post.id}/accept`,
      `/api/proposals/${post.id}/reject`,
      `/api/ingestion/proposals/${post.id}/undo`,
    ]) {
      const refused = await other.request.post(action, {
        headers: CSRF,
        data: {},
      });
      expect(refused.status()).toBe(404);
    }
    expect(itemAt(await review(), POST).status).toBe("pending");
  } finally {
    await other.close();
  }
});

test("the list in the page shows the sections, the sources and the quote in its place", async () => {
  await page.goto("/profile/fill?step=review");
  await expect(
    page.getByRole("heading", {
      level: 1,
      name: "Fill my profile with an agent",
    }),
  ).toBeVisible();
  const opened = await page.locator("[data-step]").getAttribute("data-step");
  test.skip(
    opened !== "review",
    "the review step is turned on by the task that builds the run's page (G11)",
  );
  for (const section of ["Identity", "Experience", "Skills"])
    await expect(page.getByRole("region", { name: section })).toBeVisible();
  const proposals = page.getByTestId("proposal");
  await expect(proposals).toHaveCount(4);
  const name = page.locator('[data-testid="proposal"][data-key="full_name"]');
  await expect(
    name
      .getByRole("list", { name: "Sources of Full name" })
      .getByRole("listitem"),
  ).toHaveCount(2);
  const rail = page.locator(`[data-testid="proposal"][data-key="${RAIL}"]`);
  await expect(rail).toContainText("Sources disagree");
  await expect(rail).toContainText("2019-03");
  await expect(rail).toContainText("2019-05");
  await expect(rail).toContainText(FIRST);
  await expect(rail).toContainText(SECOND);
  await expect(
    page.locator(`[data-testid="proposal"][data-key="${YEARS}"]`),
  ).toContainText("Calculated");
  await expect(page.locator("main")).not.toContainText(/[–—]| - /);

  // Open a citation and land on the quote in the document's text.
  await page
    .locator(`[data-testid="proposal"][data-key="${POST}"]`)
    .getByRole("button", { name: /open at the quote/ })
    .click();
  const quote = page.getByRole("dialog").getByTestId("cited-quote");
  await expect(quote).toBeInViewport();
  await expect(quote).toHaveText(
    "Example Post, Analyst, January 2016 to December 2018, Paris",
  );
  await page.keyboard.press("Escape");
  expect(errors).toEqual([]);
});

test("an accept is one revision of exactly that item, and an undo takes it back", async () => {
  const before = await review();
  const post = itemAt(before, POST);
  const accepted = await context.request.post(
    `/api/proposals/${post.id}/accept`,
    { headers: CSRF, data: {} },
  );
  expect(accepted.status()).toBe(200);
  const after = await review();
  expect(after.profile_revision).toBe(before.profile_revision + 1);
  expect(itemAt(after, POST)).toMatchObject({
    status: "accepted",
    applied_revision: after.profile_revision,
  });
  // That item and no other: the rest is still pending, against an untouched profile.
  for (const key of ["full_name", RAIL, YEARS])
    expect(itemAt(after, key)).toMatchObject({
      status: "pending",
      before: null,
    });

  const undone = await context.request.post(
    `/api/ingestion/proposals/${post.id}/undo`,
    { headers: CSRF },
  );
  expect(undone.status()).toBe(200);
  expect(undone.headers()["cache-control"]).toBe("no-store");
  const last = await review();
  expect(last.profile_revision).toBe(before.profile_revision + 2);
  expect(itemAt(last, POST).status).toBe("undone");
  // The years were counted from that employer: they are to be checked again.
  expect(itemAt(last, YEARS)).toMatchObject({
    status: "pending",
    stale: true,
    stale_reason: "depends_on_changed",
  });
  const again = await context.request.post(
    `/api/ingestion/proposals/${post.id}/undo`,
    { headers: CSRF },
  );
  expect(again.status()).toBe(409);
  expect(((await again.json()) as { reason: string }).reason).toBe(
    "not_accepted",
  );
});
