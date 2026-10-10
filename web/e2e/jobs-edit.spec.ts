import { execFileSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

import {
  expect,
  test,
  type APIRequestContext,
  type BrowserContext,
  type Page,
} from "@playwright/test";

import {
  caddyUrl,
  cliLink,
  makeShots,
  newContext,
  root,
  setPassword,
  watchErrors,
} from "./stack";

const shots = makeShots("jobs-j4");

/**
 * Jobs J4 against the real stack through Caddy, no mocks: editing a posting (status, notes
 * autosave, fit set by hand), multi-select with a bulk status change and its Undo, adding a
 * posting by hand and its duplicate, and "Rescore all" keeping every fit set by hand.
 *
 * Two data modes:
 * - default (local scratch stack): Harald's real profile and 433 jobs are converted on the Mac
 *   and imported with the CLIs in the api container, as `./deploy.sh profile import` and
 *   `./deploy.sh jobs import` do. A rescore of all must then leave every fit identical (the
 *   engine's fits are reproduced, the hand set ones are kept).
 *     HTTP_PORT=8114 docker compose -p jobapp-j4 -f compose.yml -f compose.mac.yml up -d --build --wait
 *     docker compose -p jobapp-j4 -f compose.yml exec -T api alembic upgrade head
 *     E2E_CADDY_URL=http://127.0.0.1:8114 E2E_COMPOSE_PROJECT=jobapp-j4 \
 *       pnpm --dir web exec playwright test jobs-edit.spec.ts
 *     docker compose -p jobapp-j4 -f compose.yml down -v
 * - E2E_SYNTHETIC=1 (the Jetson, tests/e2e_jobs_jetson.sh): a synthetic skills section and a
 *   dozen synthetic postings are loaded over HTTP into the e2e account; nothing of Harald's is
 *   read or touched.
 *
 * Screenshots never show the PDF preview or Harald's notes in full: the detail shots use a
 * posting whose notes the journey wrote itself.
 */
test.describe.configure({ mode: "serial" });
test.skip(!caddyUrl, "set E2E_CADDY_URL with the Compose stack running");

const synthetic = Boolean(process.env.E2E_SYNTHETIC);
const run = Date.now().toString(36);
const EMAIL = process.env.E2E_ADMIN_EMAIL ?? `e2e-jobs-edit-${run}@example.org`;
const NAME = "Jobs Editor";
const PASSWORD = `jobs editor ${run} passphrase`;
const CSRF = { "X-Requested-With": "jobapp" };

function compose(...args: string[]): string[] {
  return [
    "compose",
    "-p",
    process.env.E2E_COMPOSE_PROJECT ?? "jobapp",
    "-f",
    "compose.yml",
    ...args,
  ];
}

/** Convert with the api's own CLI on the Mac, then import in the api container. */
function importWithCli(module: "jobs" | "profile", source: string): string {
  const out = path.join(
    fs.mkdtempSync(path.join(os.tmpdir(), "jobs-j4-")),
    `${module}.json`,
  );
  execFileSync(
    "uv",
    [
      "run",
      "--native-tls",
      "--directory",
      path.join(root, "api"),
      "python",
      "-m",
      `app.${module}.cli`,
      "convert",
      "--source-dir",
      source,
      "--out",
      out,
    ],
    { cwd: root, stdio: ["ignore", "pipe", "inherit"] },
  );
  const document = fs.readFileSync(out);
  fs.rmSync(path.dirname(out), { recursive: true, force: true });
  return execFileSync(
    "docker",
    compose(
      "exec",
      "-T",
      "api",
      "python",
      "-m",
      `app.${module}.cli`,
      "import",
      "--email",
      EMAIL,
      "--file",
      "-",
    ),
    {
      cwd: root,
      encoding: "utf8",
      input: document,
      maxBuffer: 64 * 1024 * 1024,
    },
  );
}

const SKILLS = {
  years_experience: 3.5,
  have: [
    { name: "agents", terms: ["agent", "agentic", "tool use"] },
    { name: "python", terms: ["python", "fastapi"] },
    { name: "typescript", terms: ["typescript", "react"] },
  ],
  adjacent: [{ name: "rag", terms: ["rag", "retrieval"] }],
  none: [{ name: "research", terms: ["pytorch", "publication"] }],
  gates: [{ name: "phd_required", terms: ["phd required"], penalty: 25 }],
};

const TEXTS = [
  "Build agentic systems with tool use in Python and FastAPI.",
  "React and TypeScript front ends for an agent platform. Some RAG.",
  "Research role: PyTorch, a publication record, PhD required.",
  "Retrieval pipelines in Python, agents in production.",
];

/** A dozen synthetic postings; the first has a fit set by hand (computed it would differ). */
function syntheticDocument() {
  const jobs = Array.from({ length: 12 }, (_, i) => ({
    slug: `e2e-synthetic-${i + 1}`,
    company: `E2E Company ${(i % 4) + 1}`,
    title: `Synthetic Engineer ${i + 1}`,
    location: i % 2 ? "Paris, France" : "Remote",
    url: `https://jobs.example.org/e2e/${run}/${i + 1}`,
    status: i < 9 ? "found" : "shortlisted",
    fit: i === 0 ? 3 : null,
    posting_text: TEXTS[i % TEXTS.length],
    legacy: {
      posting_header: {
        COMPANY: `E2E Company ${(i % 4) + 1}`,
        TITLE: `Synthetic Engineer ${i + 1}`,
      },
    },
  }));
  return {
    format: "jobapp-jobs",
    version: 1,
    exported_at: new Date().toISOString(),
    jobs,
    warnings: [],
  };
}

async function loadSynthetic(request: APIRequestContext) {
  const profile = await request.get("/api/profile");
  expect(profile.ok()).toBe(true);
  const { revision } = (await profile.json()) as { revision: number };
  const saved = await request.put("/api/profile/skills", {
    headers: CSRF,
    data: { base_revision: revision, data: SKILLS },
  });
  expect(saved.ok(), await saved.text()).toBe(true);
  const imported = await request.post("/api/jobs/import", {
    headers: CSRF,
    data: syntheticDocument(),
  });
  expect(imported.ok(), await imported.text()).toBe(true);
  const summary = (await imported.json()) as {
    created: number;
    fits_manual: number;
  };
  expect(summary.created).toBe(12);
  expect(summary.fits_manual).toBe(1);
}

interface Row {
  id: string;
  slug: string;
  status: string;
  fit: number | null;
  fit_overridden: boolean;
}

/** Every row of the account through the api, page after page. */
async function allRows(request: APIRequestContext): Promise<Row[]> {
  const rows: Row[] = [];
  let cursor: string | null = null;
  do {
    const response = await request.get(
      `/api/jobs?limit=200&sort=company${cursor ? `&cursor=${encodeURIComponent(cursor)}` : ""}`,
    );
    expect(response.ok()).toBe(true);
    const page = (await response.json()) as {
      items: Row[];
      next_cursor: string | null;
    };
    rows.push(...page.items);
    cursor = page.next_cursor;
  } while (cursor);
  return rows;
}

const kpi = (page: Page, label: string) =>
  page
    .getByRole("region", { name: "Postings per status" })
    .getByRole("button", { name: new RegExp(`^${label}: `) });
const kpiCount = async (page: Page, label: string) =>
  Number(
    /: (\d+)$/.exec(
      (await kpi(page, label).getAttribute("aria-label")) ?? "",
    )?.[1],
  );
const postings = (page: Page) => page.getByRole("table", { name: "Postings" });
const rowBoxes = (page: Page) =>
  postings(page).getByRole("checkbox", { name: / at / });
const bulkBar = (page: Page) =>
  page.getByRole("region", { name: "Bulk actions" });

let context: BrowserContext;
let page: Page;
let errors: string[];
let edited: { id: string; title: string } | null = null;
let total = 0;

test.beforeAll(async ({ browser }) => {
  context = await newContext(browser);
  page = await context.newPage();
  errors = watchErrors(page, [[409, /\/api\/jobs/]]);
});

test.afterAll(async () => {
  await context.close();
});

test("an account with its skills and postings", async () => {
  test.setTimeout(240_000);
  await page.goto(cliLink(EMAIL));
  await setPassword(page, PASSWORD, NAME);
  if (synthetic) {
    await loadSynthetic(page.request);
  } else {
    expect(importWithCli("profile", path.resolve(root, ".."))).toContain(
      "revision",
    );
    const output = importWithCli("jobs", path.resolve(root, "../data"));
    expect(output).toContain("Imported 433 rows");
    expect(output).toMatch(/Fits set by hand: \d+\./);
  }
  total = (await allRows(page.request)).length;
  await page.goto("/jobs");
  await expect(page.getByRole("main").getByRole("status").first()).toHaveText(
    `${total} postings`,
  );
  await expect(
    page.getByRole("button", { name: "Add a posting" }),
  ).toBeVisible();
});

test("a detail page: status, notes autosave and fit set by hand", async () => {
  test.setTimeout(120_000);
  await page.goto("/jobs?status=found&sort=company");
  const first = postings(page).locator("[data-index='0']");
  await expect(first).toBeVisible();
  const link = first.getByRole("link");
  const title = (await link.textContent()) ?? "";
  await link.click();
  await expect(
    page.getByRole("heading", { level: 1, name: title }),
  ).toBeVisible();
  const id = page.url().split("/jobs/")[1]!;
  edited = { id, title };

  // Status: changes at once, the history follows, a reload keeps it.
  await page.getByLabel("Status", { exact: true }).selectOption("shortlisted");
  await expect(page.getByText("Status set to Shortlisted.")).toBeVisible();
  const history = page.getByRole("list", { name: "History, newest first" });
  await expect(history.getByRole("listitem").first()).toContainText(
    "Status changed from Found to Shortlisted",
  );

  // Notes: saved by themselves after a pause in typing.
  const notes = page.getByRole("textbox", { name: "Notes" });
  await notes.fill("");
  await notes.pressSequentially("E2E note: called the recruiter", {
    delay: 10,
  });
  await expect(page.getByTestId("notes-status")).toHaveText("Saved", {
    timeout: 10_000,
  });

  // Fit set by hand.
  await page.getByLabel("Fit", { exact: true }).fill("42");
  await page.getByRole("button", { name: "Set by hand" }).click();
  await expect(page.getByText("Fit set by hand to 42.")).toBeVisible();
  await expect(page.getByLabel("Fit 42 out of 100, set by hand")).toBeVisible();

  await page.reload();
  await expect(page.getByLabel("Status", { exact: true })).toHaveValue(
    "shortlisted",
  );
  await expect(page.getByRole("textbox", { name: "Notes" })).toHaveValue(
    "E2E note: called the recruiter",
  );
  await expect(page.getByLabel("Fit 42 out of 100, set by hand")).toBeVisible();
  await expect(history.getByRole("listitem").first()).toContainText(
    "Fit set by hand",
  );
  await shots(page, "detail-edited", false);

  // The applied confirmation, cancelled.
  await page.getByLabel("Status", { exact: true }).selectOption("applied");
  await expect(page.getByRole("alertdialog")).toContainText(
    "Mark this posting as applied?",
  );
  await shots(page, "detail-confirm-applied", false);
  await page.getByRole("button", { name: "Cancel" }).click();
  await expect(page.getByLabel("Status", { exact: true })).toHaveValue(
    "shortlisted",
  );

  // The details dialog.
  await page.getByRole("button", { name: "Edit details" }).click();
  const dialog = page.getByRole("dialog", { name: "Edit details" });
  await expect(dialog).toBeVisible();
  await shots(page, "detail-edit-dialog", false);
  await dialog.getByRole("button", { name: "Cancel" }).click();
  expect(errors).toEqual([]);
});

test("select five rows, set them to Shortlisted, then Undo", async () => {
  test.setTimeout(120_000);
  await page.goto("/jobs?status=found&sort=company");
  await expect(rowBoxes(page).first()).toBeVisible();
  const found = await kpiCount(page, "Found");
  const shortlisted = await kpiCount(page, "Shortlisted");
  await rowBoxes(page).nth(0).click();
  await rowBoxes(page)
    .nth(4)
    .click({ modifiers: ["Shift"] });
  await expect(bulkBar(page)).toContainText("5 postings selected");
  await shots(page, "bulk-bar", false);
  await bulkBar(page).getByLabel("New status").selectOption("shortlisted");
  await bulkBar(page).getByRole("button", { name: "Apply" }).click();
  const toast = page.getByText("5 postings set to Shortlisted.");
  await expect(toast).toBeVisible();
  await expect(kpi(page, "Found")).toHaveAccessibleName(`Found: ${found - 5}`);
  await expect(kpi(page, "Shortlisted")).toHaveAccessibleName(
    `Shortlisted: ${shortlisted + 5}`,
  );
  await shots(page, "bulk-undo-toast", false);
  await page
    .locator("[data-sonner-toast]")
    .filter({ hasText: "5 postings set to Shortlisted." })
    .getByRole("button", { name: "Undo" })
    .click();
  await expect(
    page.getByText("Undone: 5 postings back to their previous status."),
  ).toBeVisible();
  await expect(kpi(page, "Found")).toHaveAccessibleName(`Found: ${found}`);
  await expect(kpi(page, "Shortlisted")).toHaveAccessibleName(
    `Shortlisted: ${shortlisted}`,
  );
  expect(errors).toEqual([]);
});

test("select every posting of a view up to the cap", async () => {
  await page.goto("/jobs?sort=company");
  await postings(page)
    .getByRole("checkbox", { name: "Select every posting in this view" })
    .click();
  const expected = Math.min(total, 500);
  await expect(bulkBar(page)).toContainText(`${expected} postings selected`);
  await bulkBar(page).getByRole("button", { name: "Clear" }).click();
  await expect(bulkBar(page)).toHaveCount(0);
});

test("add a posting by hand, then the same one again", async () => {
  test.setTimeout(120_000);
  await page.goto("/jobs");
  await page.getByRole("button", { name: "Add a posting" }).click();
  const dialog = page.getByRole("dialog", { name: "Add a posting" });
  await dialog.getByLabel("Company").fill("E2E Hand Added");
  await dialog.getByLabel("Title").fill(`Agent Engineer ${run}`);
  await dialog.getByLabel("Location").fill("Bordeaux, France");
  await dialog
    .getByLabel("Link to the posting")
    .fill(`https://jobs.example.org/hand/${run}`);
  await dialog
    .getByLabel("Posting text")
    .fill(
      "Requirements\nBuild agentic systems with tool use in Python and TypeScript. Some RAG.",
    );
  await shots(page, "add-dialog", false);
  await dialog.getByRole("button", { name: "Add posting" }).click();
  await expect(page.getByText(/^Posting added, fit \d+\.$/)).toBeVisible();
  await expect(
    page.getByRole("heading", { level: 1, name: `Agent Engineer ${run}` }),
  ).toBeVisible();
  const addedId = page.url().split("/jobs/")[1]!;
  await expect(page.locator("header [data-tone]")).not.toHaveText("Unscored");

  await page.goto("/jobs");
  await page.getByRole("button", { name: "Add a posting" }).click();
  const again = page.getByRole("dialog", { name: "Add a posting" });
  await again.getByLabel("Company").fill("Another name");
  await again.getByLabel("Title").fill("Another title");
  await again
    .getByLabel("Link to the posting")
    .fill(`http://www.jobs.example.org/hand/${run}/?utm_source=e2e`);
  await again.getByRole("button", { name: "Add posting" }).click();
  await expect(again).toContainText("This posting is already in your list.");
  await shots(page, "add-duplicate", false);
  await again.getByRole("link", { name: "Open the existing posting" }).click();
  await expect(page).toHaveURL(new RegExp(`/jobs/${addedId}$`));
  total += 1;
});

test("Rescore all keeps every fit set by hand", async () => {
  test.setTimeout(240_000);
  const before = await allRows(page.request);
  const manual = before.filter((row) => row.fit_overridden);
  expect(manual.length).toBeGreaterThanOrEqual(synthetic ? 1 : 40);
  expect(manual.some((row) => row.id === edited?.id && row.fit === 42)).toBe(
    true,
  );
  await page.goto("/jobs");
  await page.getByRole("button", { name: "More actions" }).click();
  await page.getByRole("menuitem", { name: "Rescore all postings" }).click();
  const dialog = page.getByRole("alertdialog", {
    name: "Rescore all postings?",
  });
  await expect(dialog).toBeVisible();
  await dialog.getByLabel("Also replace the fits I set by hand").check();
  await expect(dialog).toContainText("This cannot be undone.");
  await shots(page, "rescore-confirm", false);
  await dialog.getByLabel("Also replace the fits I set by hand").uncheck();
  await dialog.getByRole("button", { name: "Rescore" }).click();
  const summary = page.getByText(/^Rescored [\d,]+ postings?: /);
  await expect(summary).toBeVisible({ timeout: 120_000 });
  const text = (await summary.textContent()) ?? "";
  console.log(`   ${text}`);
  expect(text).toContain(
    `${manual.length} ${manual.length === 1 ? "fit" : "fits"} set by hand kept`,
  );

  const after = new Map(
    (await allRows(page.request)).map((row) => [row.id, row]),
  );
  for (const row of manual) {
    expect(after.get(row.id)?.fit, row.slug).toBe(row.fit);
    expect(after.get(row.id)?.fit_overridden, row.slug).toBe(true);
  }
  if (!synthetic) {
    // Harald's real rows: every fit identical, the engine's ones reproduced from the posting.
    for (const row of before)
      expect(after.get(row.id)?.fit, row.slug).toBe(row.fit);
  }
  await shots(page, "after-rescore", false);
  expect(errors).toEqual([]);
});
