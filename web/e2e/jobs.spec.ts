import { execFileSync } from "node:child_process";
import { createHash } from "node:crypto";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

import { expect, test, type BrowserContext, type Page } from "@playwright/test";

import {
  caddyUrl,
  cliLink,
  makeShots,
  newContext,
  root,
  setPassword,
  signIn,
  watchErrors,
} from "./stack";

const shots = makeShots("jobs-j3", "jobs");

/**
 * The jobs dashboard and detail pages against the real stack through Caddy, no mocks: a fresh
 * account receives 433 rows with the CSV counts, then the journey checks the counts, filters, a
 * shared URL, infinite scroll, a detail with its PDF preview, the way back, the keyboard and the
 * phone layout.
 *
 * Two data modes:
 * - default: the job folder of E2E_JOBS_DATA (default ../data next to the app folder, Harald's;
 *   the gate gives the folder .github/scripts/synthetic_jobs.py writes) is converted on the Mac
 *   and imported with the CLI in the api container, as `./deploy.sh jobs import` does.
 *   E2E_JOBS_FILE skips the conversion and imports an already converted document. On a scratch
 *   Compose project:
 *     HTTP_PORT=8113 docker compose -p jobapp-j3 -f compose.yml -f compose.mac.yml up -d --build --wait
 *     docker compose -p jobapp-j3 -f compose.yml exec -T api alembic upgrade head
 *     E2E_CADDY_URL=http://127.0.0.1:8113 E2E_COMPOSE_PROJECT=jobapp-j3 \
 *       E2E_JOBS_DATA=/path/to/job/data pnpm --dir web exec playwright test jobs.spec.ts
 *     docker compose -p jobapp-j3 -f compose.yml down -v
 * - E2E_SYNTHETIC=1 (the Jetson, `tests/e2e_jobs_jetson.sh view`): 433 made-up rows with the
 *   same counts, posting texts and files are posted over HTTP (POST /api/jobs/import). Nothing
 *   of Harald's is read; past the account link (stack.ts cliLink, E2E_ADMIN_VIA_DEPLOY on the
 *   Jetson), everything goes through E2E_CADDY_URL.
 *
 * Screenshots never show the PDF preview: Harald's CV carries his contact details.
 */
test.describe.configure({ mode: "serial" });
test.skip(!caddyUrl, "set E2E_CADDY_URL with the Compose stack running");

const synthetic = Boolean(process.env.E2E_SYNTHETIC);
const run = Date.now().toString(36);
const EMAIL = `e2e-jobs-${run}@example.org`;
const NAME = "Jobs Journey";
const PASSWORD = `jobs journey ${run} passphrase`;
const CSRF = { "X-Requested-With": "jobapp" };

/** data/jobs.csv, 2026-10-09: 433 rows. */
const CSV_COUNTS = {
  Found: 158,
  Shortlisted: 146,
  "CV ready": 3,
  Staged: 29,
  Applied: 29,
  Interview: 2,
  Rejected: 0,
  Closed: 6,
  Skipped: 60,
} as const;
const TOTAL = 433;

const STATUS_OF: Record<keyof typeof CSV_COUNTS, string> = {
  Found: "found",
  Shortlisted: "shortlisted",
  "CV ready": "cv_ready",
  Staged: "staged",
  Applied: "applied",
  Interview: "interview",
  Rejected: "rejected",
  Closed: "closed",
  Skipped: "skipped",
};
const TITLES = [
  "AI Engineer",
  "Product Manager, Agents",
  "Forward Deployed Engineer",
  "Solutions Architect",
  "Applied Scientist",
  "Developer Advocate",
];
const CITIES = [
  "Paris, France",
  "Bordeaux, France",
  "London, UK",
  "Remote, Europe",
  "Berlin, Germany",
];

/** A valid one page PDF with one line of ASCII text, its cross-reference offsets computed. */
function tinyPdf(text: string): Buffer {
  const stream = `BT /F1 18 Tf 72 720 Td (${text}) Tj ET`;
  const objects = [
    "<< /Type /Catalog /Pages 2 0 R >>",
    "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
    "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
    `<< /Length ${stream.length} >>\nstream\n${stream}\nendstream`,
    "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
  ];
  let out = "%PDF-1.4\n";
  const offsets = objects.map((body, i) => {
    const offset = out.length;
    out += `${i + 1} 0 obj\n${body}\nendobj\n`;
    return offset;
  });
  const xref = out.length;
  out += `xref\n0 ${objects.length + 1}\n0000000000 65535 f \n`;
  for (const offset of offsets)
    out += `${String(offset).padStart(10, "0")} 00000 n \n`;
  out += `trailer\n<< /Size ${objects.length + 1} /Root 1 0 R >>\nstartxref\n${xref}\n%%EOF\n`;
  return Buffer.from(out, "ascii");
}

function importFile(name: string, mime: string, data: Buffer) {
  return {
    name,
    mime,
    size: data.length,
    sha256: createHash("sha256").update(data).digest("hex"),
    base64: data.toString("base64"),
  };
}

/**
 * The synthetic mode's import document, shaped like .github/scripts/synthetic_jobs.py: 433 rows
 * with the CSV counts, every 17th unscored, the staged, applied and interview rows with a posting
 * text, a cv.pdf and a cv.json, and one applied title in three without "engineer".
 */
function syntheticDocument() {
  const jobs: object[] = [];
  let n = 0;
  for (const [label, count] of Object.entries(CSV_COUNTS)) {
    const status = STATUS_OF[label as keyof typeof CSV_COUNTS];
    for (let i = 0; i < count; i += 1) {
      n += 1;
      const company = `Example Company ${String(n).padStart(3, "0")}`;
      const title =
        status === "applied"
          ? i % 3
            ? "Applied AI Engineer"
            : "Product Manager, Agents"
          : TITLES[n % TITLES.length];
      const files = ["staged", "applied", "interview"].includes(status);
      jobs.push({
        slug: `e2e-jobs-${n}`,
        company,
        title,
        location: CITIES[n % CITIES.length],
        url: `https://jobs.example.org/e2e-jobs/${run}/${n}`,
        status,
        fit: n % 17 ? (n * 7) % 101 : null,
        found_on: `2026-09-${String(1 + (n % 28)).padStart(2, "0")}`,
        applied_on: ["applied", "interview"].includes(status)
          ? "2026-10-01"
          : null,
        description: `Synthetic posting ${n} for the jobs journey.`,
        ...(files && {
          posting_text: `${company} is a made-up company.\n\nThe role: ${title}, also made up.`,
          files: [
            importFile(
              "cv.pdf",
              "application/pdf",
              tinyPdf(`Synthetic CV ${n}`),
            ),
            importFile(
              "cv.json",
              "application/json",
              Buffer.from(
                JSON.stringify({ target: title, headline: "Synthetic" }),
              ),
            ),
          ],
        }),
      });
    }
  }
  return {
    format: "jobapp-jobs",
    version: 1,
    exported_at: new Date().toISOString(),
    jobs,
    warnings: [],
  };
}

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

/** The import document of Harald's data, converted with the api's own CLI. */
function jobsDocument(): Buffer {
  if (process.env.E2E_JOBS_FILE)
    return fs.readFileSync(process.env.E2E_JOBS_FILE);
  const data = process.env.E2E_JOBS_DATA ?? path.resolve(root, "../data");
  const out = path.join(
    fs.mkdtempSync(path.join(os.tmpdir(), "jobs-j3-")),
    "jobs.json",
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
      "app.jobs.cli",
      "convert",
      "--source-dir",
      data,
      "--out",
      out,
    ],
    { cwd: root, stdio: ["ignore", "pipe", "inherit"] },
  );
  const document = fs.readFileSync(out);
  fs.rmSync(path.dirname(out), { recursive: true, force: true });
  return document;
}

function importJobs(email: string): string {
  return execFileSync(
    "docker",
    compose(
      "exec",
      "-T",
      "api",
      "python",
      "-m",
      "app.jobs.cli",
      "import",
      "--email",
      email,
      "--file",
      "-",
    ),
    {
      cwd: root,
      encoding: "utf8",
      input: jobsDocument(),
      maxBuffer: 64 * 1024 * 1024,
    },
  );
}

const kpi = (page: Page, label: string) =>
  page
    .getByRole("region", { name: "Postings per status" })
    .getByRole("button", { name: new RegExp(`^${label}: `) });
const summary = (page: Page) =>
  page.getByRole("main").getByRole("status").first();
const postings = (page: Page) => page.getByRole("table", { name: "Postings" });
const bodyRows = (page: Page) => postings(page).locator("[data-index]");

let context: BrowserContext;
let page: Page;
let errors: string[];

test.beforeAll(async ({ browser }) => {
  context = await newContext(browser);
  page = await context.newPage();
  errors = watchErrors(page);
});

test.afterAll(async () => {
  await context.close();
});

test("a new account starts empty, then holds the 433 rows with the CSV counts", async () => {
  test.setTimeout(180_000);
  await page.goto(cliLink(EMAIL));
  await setPassword(page, PASSWORD, NAME);
  await expect(
    page.getByRole("heading", { level: 1, name: "Jobs" }),
  ).toBeVisible();
  await expect(page.getByText("No postings yet")).toBeVisible();
  await expect(
    page.getByRole("navigation", { name: "Main" }).getByRole("link").first(),
  ).toHaveText("Jobs");
  await expect(page.getByRole("contentinfo").getByRole("status")).toContainText(
    "API ok",
  );
  await shots(page, "empty");

  if (synthetic) {
    const imported = await page.request.post("/api/jobs/import", {
      headers: CSRF,
      data: syntheticDocument(),
      timeout: 120_000,
    });
    expect(imported.ok(), await imported.text()).toBe(true);
    expect(((await imported.json()) as { created: number }).created).toBe(
      TOTAL,
    );
  } else {
    expect(importJobs(EMAIL)).toContain(`Imported ${TOTAL} rows`);
  }

  await page.reload();
  await expect(summary(page)).toHaveText(`${TOTAL} postings`);
  for (const [label, count] of Object.entries(CSV_COUNTS))
    await expect(kpi(page, label)).toHaveAccessibleName(`${label}: ${count}`);
  // The histogram counts every row: its buckets plus the unscored add up to 433.
  const bars = page
    .getByRole("list", { name: "Postings per fit range" })
    .getByRole("button");
  await expect(bars).toHaveCount(10);
  let histogram = 0;
  for (const name of await bars.evaluateAll((els) =>
    els.map((el) => el.getAttribute("aria-label") ?? ""),
  ))
    histogram += Number(/: (\d+) postings$/.exec(name)?.[1]);
  const unscored = Number(
    /(\d+) unscored/.exec(
      (await page.getByText(/\d+ unscored/).textContent()) ?? "",
    )?.[1],
  );
  expect(histogram + unscored).toBe(TOTAL);
  await expect(bodyRows(page).first()).toBeVisible();
  await shots(page, "dashboard", false);
  expect(errors).toEqual([]);
});

let sharedUrl = "";

test("filter by status and search; the URL holds the view and opens the same in another browser", async ({
  browser,
}) => {
  await kpi(page, "Applied").click();
  await expect(page).toHaveURL(/\/jobs\?status=applied$/);
  await expect(summary(page)).toHaveText("29 postings match");
  await expect(kpi(page, "Applied")).toHaveAttribute("aria-pressed", "true");
  // Every visible row is applied.
  await expect(bodyRows(page).first()).toBeVisible();
  const statuses = await bodyRows(page)
    .locator("[data-status]")
    .evaluateAll((els) => els.map((el) => el.getAttribute("data-status")));
  expect(new Set(statuses)).toEqual(new Set(["applied"]));

  await page
    .getByRole("searchbox", { name: "Search postings" })
    .fill("engineer");
  await expect(page).toHaveURL(/\/jobs\?q=engineer&status=applied$/);
  const expected = (await (
    await page.request.get("/api/jobs?status=applied&q=engineer&limit=1")
  ).json()) as { total: number };
  expect(expected.total).toBeGreaterThan(0);
  expect(expected.total).toBeLessThan(29);
  await expect(summary(page)).toHaveText(
    `${expected.total} ${expected.total === 1 ? "posting" : "postings"} match`,
  );
  await expect(
    page.getByRole("list", { name: "Active filters" }).getByRole("listitem"),
  ).toHaveCount(2);
  await shots(page, "filtered", false);
  sharedUrl = page.url();

  // The copied URL, opened in a fresh browser: sign in, then the very same view.
  const other = await newContext(browser);
  try {
    const second = await other.newPage();
    const otherErrors = watchErrors(second);
    await second.goto(sharedUrl);
    await expect(
      second.getByRole("heading", { name: "Sign in" }),
    ).toBeVisible();
    await second.getByLabel("Email").fill(EMAIL);
    await second.getByLabel("Password").fill(PASSWORD);
    await second.getByRole("button", { name: "Sign in" }).click();
    await expect(second).toHaveURL(sharedUrl);
    await expect(summary(second)).toHaveText(
      `${expected.total} ${expected.total === 1 ? "posting" : "postings"} match`,
    );
    await expect(
      second.getByRole("searchbox", { name: "Search postings" }),
    ).toHaveValue("engineer");
    await expect(kpi(second, "Applied")).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    expect(otherErrors).toEqual([]);
  } finally {
    await other.close();
  }

  // Back and forward walk through the views.
  await page.goBack();
  await expect(page).toHaveURL(/\/jobs\?status=applied$/);
  await expect(
    page.getByRole("searchbox", { name: "Search postings" }),
  ).toHaveValue("");
  await page.goForward();
  await expect(page).toHaveURL(sharedUrl);
  expect(errors).toEqual([]);
});

test("scrolling loads all 433 rows page by page, with only a window in the DOM", async () => {
  test.setTimeout(90_000);
  const cursors: string[] = [];
  page.on("request", (request) => {
    const url = new URL(request.url());
    if (url.pathname === "/api/jobs" && url.searchParams.has("cursor"))
      cursors.push(url.searchParams.get("cursor") ?? "");
  });
  await page.goto("/jobs");
  await expect(summary(page)).toHaveText(`${TOTAL} postings`);
  const last = postings(page).locator(`[aria-rowindex="${TOTAL + 1}"]`);
  for (let i = 0; i < 40 && !(await last.isVisible()); i += 1) {
    await page.evaluate(() => {
      window.scrollTo(0, document.documentElement.scrollHeight);
    });
    await page.waitForTimeout(300);
  }
  await expect(last).toBeVisible();
  // 433 rows at 100 per page: four more pages after the first, then nothing.
  expect(cursors).toHaveLength(4);
  await expect(
    page.getByText(/Load more postings|Loading more postings/),
  ).toHaveCount(0);
  expect(await bodyRows(page).count()).toBeLessThan(80);
  await page.evaluate(() => {
    window.scrollTo(0, 0);
  });
  expect(errors).toEqual([]);
});

test("a detail with its posting, files, PDF preview and history; back keeps the filters", async () => {
  test.setTimeout(90_000);
  // An applied row with a cv.pdf and a posting text, found through the api.
  const list = (await (
    await page.request.get("/api/jobs?status=applied&limit=50")
  ).json()) as { items: Array<{ id: string; company: string }> };
  let target: { id: string; company: string; title: string } | undefined;
  for (const item of list.items) {
    const detail = (await (
      await page.request.get(`/api/jobs/${item.id}`)
    ).json()) as {
      title: string;
      posting_text: string | null;
      files: Array<{ name: string }>;
    };
    if (detail.posting_text && detail.files.some((f) => f.name === "cv.pdf")) {
      target = { id: item.id, company: item.company, title: detail.title };
      break;
    }
  }
  expect(target).toBeDefined();
  const job = target!;

  const listUrl = `/jobs?status=applied&company=${encodeURIComponent(job.company)}`;
  await page.goto(listUrl);
  await expect(bodyRows(page).first()).toBeVisible();
  await postings(page).locator(`a[href="/jobs/${job.id}"]`).click();
  await expect(page).toHaveURL(new RegExp(`/jobs/${job.id}$`));
  await expect(
    page.getByRole("heading", { level: 1, name: job.title }),
  ).toBeVisible();
  await expect(
    page.getByText("Applied", { exact: true }).first(),
  ).toBeVisible();
  await expect(
    page.getByTestId("posting-text").locator("p").first(),
  ).toBeVisible();
  const files = page.getByRole("list", { name: "Files" });
  await expect(files).toContainText("cv.pdf");
  await expect(
    page.getByRole("list", { name: "History, newest first" }),
  ).toContainText("Imported");
  await shots(page, "detail");

  // The PDF preview: fetched with the session, shown from a blob in an iframe.
  const pdf = page.waitForResponse(
    (r) => /\/api\/jobs\/[^/]+\/files\//.test(r.url()) && r.status() === 200,
  );
  await page.getByRole("button", { name: "Show preview of cv.pdf" }).click();
  expect((await pdf).headers()["content-type"]).toBe("application/pdf");
  const frame = page.getByTitle("Preview of cv.pdf");
  await expect(frame).toBeVisible();
  await expect(frame).toHaveAttribute("src", /^blob:/);
  expect((await frame.boundingBox())?.height ?? 0).toBeGreaterThan(300);
  await page.getByRole("button", { name: "Hide preview of cv.pdf" }).click();
  await expect(frame).toHaveCount(0);

  // A JSON file, pretty-printed on demand; every synthetic row with files has one.
  const json = files.getByText(/^Show \w+\.json$/).first();
  if (synthetic) await expect(json).toBeVisible();
  if (await json.count()) {
    await json.click();
    await expect(
      files.getByLabel(/^Content of \w+\.json$/).first(),
    ).toContainText("{");
  }

  await page.getByRole("link", { name: "Back to jobs" }).click();
  const query = new URLSearchParams({
    status: "applied",
    company: job.company,
  });
  await expect
    .poll(() => {
      const url = new URL(page.url());
      return `${url.pathname}?${url.searchParams.toString()}`;
    })
    .toBe(`/jobs?${query.toString()}`);
  await expect(
    page.getByRole("list", { name: "Active filters" }),
  ).toContainText(`Company: ${job.company}`);
  expect(errors).toEqual([]);
});

test("keyboard: arrows move between rows and Enter opens one", async () => {
  await page.goto("/jobs?sort=fit");
  const first = bodyRows(page).first();
  await expect(first).toBeVisible();
  await first.focus();
  await page.keyboard.press("ArrowDown");
  await page.keyboard.press("ArrowDown");
  await expect(bodyRows(page).nth(2)).toBeFocused();
  const href = await bodyRows(page).nth(2).locator("a").getAttribute("href");
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(new RegExp(`${href ?? "none"}$`));
  await page.goBack();
  await expect(page).toHaveURL(/\/jobs\?sort=fit$/);
  expect(errors).toEqual([]);
});

test("on a phone the rows are cards and nothing overflows", async () => {
  await page.setViewportSize({ width: 375, height: 812 });
  await page.goto("/jobs?status=shortlisted");
  await expect(summary(page)).toHaveText("146 postings match");
  const card = bodyRows(page).first();
  await expect(card).toBeVisible();
  // The header row is for screen readers only; each row is a bordered card.
  const header = postings(page).getByRole("rowgroup").first();
  await expect(header).toContainText("Company");
  expect((await header.boundingBox())?.width ?? 0).toBeLessThanOrEqual(1);
  const inner = card.locator(":scope > div");
  await expect(inner).toHaveCSS("border-top-style", "solid");
  const box = await inner.boundingBox();
  expect(box?.width ?? 999).toBeLessThanOrEqual(375 - 32);
  expect(
    await page.evaluate(
      () =>
        document.documentElement.scrollWidth -
        document.documentElement.clientWidth,
    ),
  ).toBe(0);
  await page.evaluate(() => {
    window.scrollTo(0, 1100);
  });
  await page.waitForTimeout(300);
  await page.screenshot({
    path: path.join(
      root,
      "tasks/evidence",
      process.env.E2E_EVIDENCE ?? "jobs-j3",
      `${(process.env.E2E_SHOT_PREFIX ?? "").replace("{spec}", "jobs")}cards-mobile.png`,
    ),
    animations: "disabled",
  });
  await page.setViewportSize({ width: 1280, height: 800 });
  expect(errors).toEqual([]);
});

test("signing in lands on the jobs dashboard", async ({ browser }) => {
  const other = await newContext(browser);
  try {
    const fresh = await other.newPage();
    await signIn(fresh, EMAIL, PASSWORD);
    await expect(fresh).toHaveURL(/\/jobs$/);
    await expect(summary(fresh)).toHaveText(`${TOTAL} postings`);
  } finally {
    await other.close();
  }
});
