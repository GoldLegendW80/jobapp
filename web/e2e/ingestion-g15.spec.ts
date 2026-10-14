import { readFileSync } from "node:fs";
import path from "node:path";

import { expect, test, type BrowserContext, type Page } from "@playwright/test";

import {
  caddyUrl,
  clearAttempts,
  cliLink,
  makeShots,
  newContext,
  root,
  setPassword,
  watchErrors,
} from "./stack";

/**
 * Task G15 of the ingestion module against the real stack through Caddy, no mocks, with the
 * fake backend (AGENTS_FAKE_BACKEND=true, never in production): reruns remember, and deleting a
 * document. A new user runs on two documents (the notes of the G13 journey and the first
 * document of G12), rejects one item and accepts another, uploads a third document and runs
 * again: only that document is read, the rejected and the accepted items do not come back.
 * Then the notes are deleted from the page after reading the dialog: their pending items
 * disappear, an accepted item shows "Source deleted" with no quote, and the run shows the
 * document as deleted. The same notes uploaded again and read again: still nothing decided is
 * proposed again.
 *
 * It sets up its own user, documents and runs, and depends on no other journey.
 *
 * Locally, on a scratch Compose project (its own volumes and images, torn down afterwards):
 *   .github/scripts/ci_env.sh 8115 >.env && echo AGENTS_FAKE_BACKEND=true >>.env
 *   docker compose -p jobapp-g15 -f compose.yml -f compose.mac.yml up -d --build --wait
 *   docker compose -p jobapp-g15 -f compose.yml exec -T api alembic upgrade head
 *   E2E_CADDY_URL=http://127.0.0.1:8115 E2E_COMPOSE_PROJECT=jobapp-g15 \
 *     pnpm --dir web exec playwright test -c e2e/ingestion-all.config.ts ingestion-g15
 *   docker compose -p jobapp-g15 -f compose.yml down -v
 */
test.describe.configure({ mode: "serial" });
test.skip(!caddyUrl, "set E2E_CADDY_URL with the Compose stack running");

const stamp = Date.now().toString(36);
const EMAIL = `e2e-ingestion-g15-${stamp}@example.org`;
const NAME = "Sam Example";
const PASSWORD = `ingestion delete ${stamp} passphrase`;
const tail = () => Math.random().toString(36).slice(2, 12).padEnd(10, "x");
const KEY = `sk-ant-api03-e2egood${tail()}${tail()}Rn42`;

const RUNS = "/api/ingestion/runs";
const CSRF = { "X-Requested-With": "jobapp" };
const FIXTURES = path.join(root, "api/tests/ingestion/fixtures");
const NOTES = "g13_notes.txt";
const CV = "g12_first.txt";
const LETTER = "g12_second.txt";
const POST = "employers:example-post-2016";
const DELETE_PROMISE =
  "Gone from the app now, and from the server's backups within 7 days.";
const DELETE_PROVIDER =
  "It does not delete what Anthropic has already received.";

const shots = makeShots("ingestion-g15", "g15");

interface Step {
  status: string;
  document_name: string | null;
}
interface Run {
  status: string;
  counts: { proposals: number };
  steps: Step[];
}
interface Citation {
  document: unknown;
  excerpt: string;
  source_deleted: boolean;
}
interface Item {
  key: string;
  status: string;
  citations: Citation[];
}

let context: BrowserContext;
let page: Page;
let errors: string[];
let firstRun = "";

test.beforeAll(async ({ browser }) => {
  context = await newContext(browser);
  page = await context.newPage();
  errors = watchErrors(page);
});

test.afterAll(async () => {
  await context.close();
  clearAttempts([EMAIL]);
});

const proposal = (key: string) =>
  page.locator(`[data-testid="proposal"][data-key="${key}"]`);
const rows = () =>
  page.getByRole("list", { name: "Your documents" }).getByRole("listitem");

async function documentIds(): Promise<Map<string, string>> {
  const listed = await context.request.get("/api/ingestion/documents");
  expect(listed.status()).toBe(200);
  const { items } = (await listed.json()) as {
    items: { id: string; name: string }[];
  };
  return new Map(items.map((one) => [one.name, one.id]));
}

async function upload(names: string[]) {
  await page.goto("/profile/fill?step=documents");
  await page.getByTestId("document-files").setInputFiles(
    names.map((name) => ({
      name,
      mimeType: "text/plain",
      buffer: readFileSync(path.join(FIXTURES, name)),
    })),
  );
  for (const name of names)
    await expect(rows().filter({ hasText: name })).toContainText("Ready");
}

/** A run on these documents through the API, as the page starts one, read to its end. */
async function runOn(names: string[]): Promise<{ id: string; run: Run }> {
  const known = await documentIds();
  const ids = names.map((name) => known.get(name) ?? "");
  const estimate = async () => {
    const response = await context.request.post(`${RUNS}/estimate`, {
      headers: CSRF,
      data: { document_ids: ids, reread_document_ids: [] },
    });
    expect(response.status()).toBe(200);
    return (await response.json()) as {
      consent: { version: number; accepted: boolean };
      quote_id: string;
    };
  };
  const quote = await estimate();
  if (!quote.consent.accepted) {
    const given = await context.request.post("/api/ingestion/consent", {
      headers: CSRF,
      data: { version: quote.consent.version },
    });
    expect(given.status()).toBe(200);
  }
  const started = await context.request.post(RUNS, {
    headers: CSRF,
    data: {
      document_ids: ids,
      reread_document_ids: [],
      quote_id: (await estimate()).quote_id,
    },
  });
  expect(started.status()).toBe(201);
  const id = ((await started.json()) as { id: string }).id;
  let run: Run | undefined;
  await expect
    .poll(
      async () => {
        run = (await (
          await context.request.get(`${RUNS}/${id}`)
        ).json()) as Run;
        return run.status;
      },
      {
        message: "the run ends",
        timeout: 150_000,
        intervals: [250, 500, 1_000],
      },
    )
    .toBe("done");
  if (run === undefined) throw new Error("no run read");
  return { id, run };
}

async function items(runId: string): Promise<Item[]> {
  const response = await context.request.get(`${RUNS}/${runId}/proposals`);
  expect(response.status()).toBe(200);
  const review = (await response.json()) as {
    sections: { items: Item[] }[];
  };
  return review.sections.flatMap((one) => one.items);
}

async function openReview() {
  await page.goto("/profile/fill?step=review");
  await expect(page.getByTestId("proposal").first()).toBeVisible();
}

test("a run on two documents, one item rejected and another accepted", async () => {
  test.setTimeout(240_000);
  await page.goto(cliLink(EMAIL));
  await setPassword(page, PASSWORD, NAME);
  const saved = await context.request.put("/api/me/model-credential", {
    headers: CSRF,
    data: { kind: "anthropic_api_key", secret: KEY },
  });
  expect(saved.status()).toBe(200);

  await upload([NOTES, CV]);
  const { id, run } = await runOn([NOTES, CV]);
  firstRun = id;
  // The name, one employer, the derived years, the headline and four answers.
  expect(run.counts.proposals).toBe(8);

  await openReview();
  const travel = proposal("travel");
  await travel.getByRole("button", { name: "Reject: Travel" }).click();
  await expect(travel).toHaveAttribute("data-status", "rejected");
  const notice = proposal("notice_period");
  await notice.getByRole("button", { name: "Accept: Notice period" }).click();
  await expect(notice).toHaveAttribute("data-status", "accepted");
});

test("a third document and a rerun: only it is read, nothing decided comes back", async () => {
  test.setTimeout(240_000);
  await upload([LETTER]);
  const { id, run } = await runOn([NOTES, CV, LETTER]);
  // The two documents already read are reused: only the new one is read by the model.
  const statuses = Object.fromEntries(
    run.steps.map((step) => [step.document_name ?? "", step.status]),
  );
  expect(statuses).toEqual({
    [NOTES]: "reused",
    [CV]: "reused",
    [LETTER]: "done",
  });

  const found = await items(id);
  const keys = found.map((one) => one.key);
  expect(keys).toContain(POST); // what the new document adds
  expect(keys).toContain("relocation"); // still undecided, so proposed again
  expect(keys).not.toContain("travel"); // rejected
  expect(keys).not.toContain("notice_period"); // accepted, equal to the profile

  await openReview();
  await expect(proposal("travel")).toHaveCount(0);
  await expect(proposal("notice_period")).toHaveCount(0);
  await expect(proposal(POST)).toBeVisible();
  const headline = proposal("headline");
  await headline.getByRole("button", { name: "Accept: Headline" }).click();
  await expect(headline).toHaveAttribute("data-status", "accepted");
});

test("delete the notes after reading the dialog: what goes and what stays", async () => {
  await page.goto("/profile/fill?step=documents");
  await rows()
    .filter({ hasText: NOTES })
    .getByRole("button", { name: `Delete ${NOTES}` })
    .click();
  const dialog = page.getByRole("alertdialog", {
    name: "Delete this document?",
  });
  await expect(dialog).toContainText(NOTES);
  await expect(dialog).toContainText(DELETE_PROMISE);
  await expect(dialog).toContainText(DELETE_PROVIDER);
  await expect(dialog).not.toContainText(/[–—]| - /);
  await shots(page, "delete-dialog", false);
  await page.setViewportSize({ width: 1280, height: 800 });
  await page.emulateMedia({ colorScheme: "light" });
  await dialog.getByRole("button", { name: "Delete for good" }).click();
  await expect(dialog).toHaveCount(0);
  await expect(rows().filter({ hasText: NOTES })).toHaveCount(0);

  await openReview();
  // Its pending items are gone: those two answers were cited by the notes alone.
  await expect(proposal("relocation")).toHaveCount(0);
  await expect(proposal("earliest_start")).toHaveCount(0);
  // The accepted headline stays, its source marked deleted, with no quote left.
  const headline = proposal("headline");
  await expect(headline).toHaveAttribute("data-status", "accepted");
  const [source] = await headline
    .getByRole("list", { name: "Sources of Headline" })
    .getByRole("listitem")
    .all();
  await expect(source!).toHaveText("Source deleted");
  await expect(source!.locator("q")).toHaveCount(0);
  await expect(
    page.getByRole("list", { name: "Documents read" }),
  ).toContainText("Deleted");
  await shots(page, "review-after-delete");

  // The run shows the document as deleted, every step row kept.
  await page.getByText("Every step of this run").click();
  await expect(page.getByText(/Deleted document\./)).toBeVisible();

  // The item accepted in the first run keeps its value and only the mark of its source; the
  // rejected one is reduced to what a rerun needs and leaves that run's list.
  const first = await items(firstRun);
  const notice = first.find((one) => one.key === "notice_period");
  expect(notice?.status).toBe("accepted");
  expect(notice?.citations).toHaveLength(1);
  expect(notice?.citations[0]).toMatchObject({
    document: null,
    excerpt: "",
    source_deleted: true,
  });
  expect(first.map((one) => one.key)).not.toContain("travel");
  const profile = (await (
    await context.request.get("/api/profile")
  ).json()) as {
    sections: Record<string, Record<string, unknown>>;
  };
  expect(profile.sections.answers?.notice_period).toBe("Two months");
});

test("the same notes uploaded again and read again: what was decided is not proposed", async () => {
  test.setTimeout(240_000);
  await upload([NOTES]);
  const { id } = await runOn([NOTES, CV, LETTER]);
  const keys = (await items(id)).map((one) => one.key);
  expect(keys).toContain("relocation"); // deleted while pending, never decided
  expect(keys).not.toContain("travel");
  expect(keys).not.toContain("notice_period");
  expect(keys).not.toContain("headline");
  expect(errors).toEqual([]);
});
