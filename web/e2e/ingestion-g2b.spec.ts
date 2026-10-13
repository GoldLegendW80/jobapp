import { execFileSync } from "node:child_process";

import { expect, test, type BrowserContext, type Page } from "@playwright/test";

import { cutPdf, makePdf, type PdfPage } from "./ingestion-pdf";
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
 * Task G2b of the ingestion module against the real stack through Caddy, no mocks: a PDF with a
 * text layer is read by the worker through the parser service while the page watches. A text
 * PDF and a restricted one (RC4, empty password) go from "Being read" to "Ready" without a
 * reload, their previews show the text and a link; a PDF behind a real password and one cut
 * short each say why they were not read; a PDF with two scanned pages keeps its text and says
 * what was left. Then what the page cannot show: no log holds a word of a document.
 * Every document is made up (a fictitious person), written by ./ingestion-pdf.ts.
 *
 * Locally, on a scratch Compose project (its own volumes, torn down afterwards):
 *   HTTP_PORT=8097 docker compose -p jobapp-g2b -f compose.yml -f compose.mac.yml up -d --build --wait
 *   docker compose -p jobapp-g2b -f compose.yml exec -T api alembic upgrade head
 *   E2E_CADDY_URL=http://127.0.0.1:8097 E2E_COMPOSE_PROJECT=jobapp-g2b \
 *     pnpm --dir web exec playwright test -c e2e/ingestion-all.config.ts
 *   docker compose -p jobapp-g2b -f compose.yml down -v
 */
test.describe.configure({ mode: "serial" });
test.skip(!caddyUrl, "set E2E_CADDY_URL with the Compose stack running");

const run = Date.now().toString(36);
const project = process.env.E2E_COMPOSE_PROJECT;
const EMAIL = `e2e-ingestion-g2b-${run}@example.org`;
const NAME = "Robin Example";
const PASSWORD = `ingestion reading ${run} passphrase`;
const LIST = "/api/ingestion/documents";
const MIB = 1024 * 1024;

// Words no log may hold: one in a file name, one in the text of a PDF.
const MARKER_NAME = `zq-pdfname-${run}`;
const MARKER_TEXT = `zq-pdftext-${run}`;

const SENTENCE =
  "Robin Example led the migration of the billing service to Postgres in 2021.";
const LINK = "https://example.org/robin-example";

const textPage = (number: number, of: number): PdfPage => ({
  lines: [
    `Robin Example, page ${number} of ${of}`,
    SENTENCE,
    `Stack: Python, FastAPI, Redis, React. Reference ${MARKER_TEXT}.`,
  ],
  links: number === 1 ? [LINK] : [],
});
const textPages = (count: number) =>
  Array.from({ length: count }, (_, index) => textPage(index + 1, count));

const CV = `CV ${MARKER_NAME}.pdf`;
const RESTRICTED = "restricted CV.pdf";
const LOCKED = "locked CV.pdf";
const CUT = "cut short.pdf";
const SCANNED = "letter with scans.pdf";

const takeShots = makeShots("ingestion-g2b", "ingestion-g2b");

/** Screenshots once every toast is gone, from the top, with the sticky header in the flow. */
async function shots(page: Page, name: string, fullPage = true) {
  await expect(page.locator("[data-sonner-toast]")).toHaveCount(0, {
    timeout: 15_000,
  });
  if (!fullPage) {
    await takeShots(page, name, false);
    return;
  }
  await page.evaluate(() => {
    window.scrollTo(0, 0);
    const style = document.createElement("style");
    style.id = "e2e-header-in-flow";
    style.textContent = "header.sticky { position: static !important; }";
    document.head.append(style);
  });
  try {
    await takeShots(page, name, true);
  } finally {
    await page.evaluate(() => {
      document.getElementById("e2e-header-in-flow")?.remove();
    });
  }
}

const pdf = (name: string, buffer: Buffer) => ({
  name,
  mimeType: "application/pdf",
  buffer,
});
const documents = (page: Page) =>
  page.getByRole("list", { name: "Your documents" });
const row = (page: Page, name: string) =>
  documents(page).getByRole("listitem").filter({ hasText: name });
const picker = (page: Page) => page.getByTestId("document-files");

function compose(...args: string[]): string {
  if (!project) throw new Error("E2E_COMPOSE_PROJECT is not set");
  return execFileSync(
    "docker",
    ["compose", "-p", project, "-f", "compose.yml", ...args],
    { cwd: root, encoding: "utf8", maxBuffer: 256 * MIB },
  );
}

/** How many times the page asked for the list during `ms`. */
async function listRequests(page: Page, ms: number): Promise<number> {
  let count = 0;
  const seen = (request: { method(): string; url(): string }) => {
    if (request.method() === "GET" && new URL(request.url()).pathname === LIST)
      count += 1;
  };
  page.on("request", seen);
  await page.waitForTimeout(ms);
  page.off("request", seen);
  return count;
}

let context: BrowserContext;
let page: Page;
let errors: string[];
let workerStopped = false;

test.beforeAll(async ({ browser }) => {
  context = await newContext(browser);
  page = await context.newPage();
  errors = watchErrors(page);
});

test.afterAll(async () => {
  // Whatever happened, the stack is left with its worker running.
  if (workerStopped) compose("start", "worker");
  await context.close();
  clearAttempts([EMAIL]);
});

test("a new user opens the documents step", async () => {
  await page.goto(cliLink(EMAIL));
  await setPassword(page, PASSWORD, NAME);
  // The page opens on step 1 (the prompt) since G3: this journey is about the documents.
  await page.goto("/profile/fill?step=documents");
  await expect(
    page.getByRole("heading", { level: 2, name: "Step 2: Add your documents" }),
  ).toBeVisible();
  await expect(
    page.getByText("No document yet. Add a file or paste a text above."),
  ).toBeVisible();
});

test("a PDF waits as Being read while the worker is away, and the page keeps asking", async () => {
  // With the worker stopped the job stays in the queue: the state a user sees for a moment
  // on a busy server, held here for as long as the test needs to look at it.
  compose("stop", "worker");
  workerStopped = true;
  await picker(page).setInputFiles([
    pdf(CV, makePdf(textPages(12))),
    pdf(RESTRICTED, makePdf(textPages(2), { userPassword: "" })),
  ]);
  await expect(row(page, CV)).toContainText("Being read", { timeout: 30_000 });
  await expect(row(page, RESTRICTED)).toContainText("Being read", {
    timeout: 30_000,
  });
  // Not read yet: no text to preview, and nothing claims a later version will read it.
  await expect(
    row(page, CV).getByRole("button", { name: /^Preview/ }),
  ).toHaveCount(0);
  await expect(row(page, CV)).not.toContainText("This version cannot read");
  // The list is asked again every 3 seconds while a document is being read.
  expect(await listRequests(page, 7_500)).toBeGreaterThanOrEqual(2);
  await shots(page, "01-being-read");
});

test("both are read once the worker is back, without a reload", async () => {
  test.setTimeout(180_000);
  compose("start", "worker");
  workerStopped = false;
  const cv = row(page, CV);
  await expect(cv).toContainText("Ready", { timeout: 120_000 });
  await expect(cv).toContainText("12 pages read as text");
  const restricted = row(page, RESTRICTED);
  await expect(restricted).toContainText("Ready", { timeout: 120_000 });
  await expect(restricted).toContainText("2 pages read as text");
  // Nothing is being read any more: the page stops asking.
  await page.waitForTimeout(3_500);
  expect(await listRequests(page, 4_500)).toBe(0);
  await shots(page, "02-read");
});

test("the previews show the text of each PDF, and its link", async () => {
  await row(page, CV)
    .getByRole("button", { name: `Preview ${CV}` })
    .click();
  const dialog = page.getByRole("dialog", { name: CV });
  const text = dialog.getByRole("region", { name: "Text of the document" });
  await expect(text).toContainText("Robin Example, page 1 of 12");
  await expect(text).toContainText(SENTENCE);
  await expect(text).toContainText(`Links: ${LINK}`);
  await expect(text).toContainText("Robin Example, page 12 of 12");
  await shots(page, "03-preview", false);
  await page.keyboard.press("Escape");
  await expect(dialog).toHaveCount(0);

  // The restricted file was opened with the empty password: its text is there too.
  await row(page, RESTRICTED)
    .getByRole("button", { name: `Preview ${RESTRICTED}` })
    .click();
  const second = page.getByRole("dialog", { name: RESTRICTED });
  await expect(
    second.getByRole("region", { name: "Text of the document" }),
  ).toContainText(SENTENCE);
  await page.keyboard.press("Escape");
  await expect(second).toHaveCount(0);
});

test("a PDF behind a password and one cut short each say why they were not read", async () => {
  const whole = makePdf(textPages(3));
  await picker(page).setInputFiles([
    pdf(LOCKED, makePdf(textPages(2), { userPassword: "a real password" })),
    pdf(CUT, cutPdf(whole)),
  ]);
  const locked = row(page, LOCKED);
  await expect(locked).toContainText("Could not be read", { timeout: 60_000 });
  await expect(locked).toContainText(
    "This PDF is protected by a password. Remove the protection, export it again, and add the new file.",
  );
  const cut = row(page, CUT);
  await expect(cut).toContainText("Could not be read", { timeout: 60_000 });
  await expect(cut).toContainText(
    "This PDF could not be read. It may be damaged: export it again from the application that made it, and add the new file.",
  );
  // Nothing was read: no preview; a failed row can be deleted.
  for (const failed of [locked, cut]) {
    await expect(failed.getByRole("button", { name: /^Preview/ })).toHaveCount(
      0,
    );
    await expect(failed.getByRole("button", { name: /^Delete/ })).toBeVisible();
  }
});

test("a PDF with two scanned pages keeps its text and says its scans wait for the agent", async () => {
  await picker(page).setInputFiles([
    pdf(
      SCANNED,
      makePdf([
        textPage(1, 5),
        { scan: true },
        textPage(3, 5),
        // A scan with a page number under it is still a scan.
        { scan: true, lines: ["Page 4 of 5"] },
        textPage(5, 5),
      ]),
    ),
  ]);
  // Until G8 this PDF ended "Ready" with a note ("2 pages hold no text and wait for the image
  // reader."). Since G8 its two scanned pages are rendered to page images, and the document
  // waits for the agent that will read them: the row says so in plain words, and the three
  // pages already read as text are kept and shown at once.
  const scanned = row(page, SCANNED);
  await expect(scanned).toContainText("Waiting for the agent", {
    timeout: 60_000,
  });
  await expect(scanned).toContainText(
    "Its scanned pages will be read by the agent at the next run.",
  );
  await expect(scanned).toContainText("3 pages read as text, 2 as images");
  await expect(scanned).not.toContainText("wait for the image reader");
  await expect(scanned).not.toContainText("Ready");
  await scanned.getByRole("button", { name: `Preview ${SCANNED}` }).click();
  const dialog = page.getByRole("dialog", { name: SCANNED });
  const text = dialog.getByRole("region", { name: "Text of the document" });
  await expect(text).toContainText("Robin Example, page 1 of 5");
  await expect(text).toContainText("Robin Example, page 3 of 5");
  await expect(text).toContainText("Robin Example, page 5 of 5");
  // A scan with a page number under it is kept as an image: the image holds that line.
  await expect(text).not.toContainText("Page 4 of 5");
  await expect(dialog.getByRole("img")).toHaveCount(2);
  await expect(
    dialog.getByRole("img", { name: "Page 2, kept as an image" }),
  ).toBeVisible();
  await expect(
    dialog.getByRole("img", { name: "Page 4, kept as an image" }),
  ).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(dialog).toHaveCount(0);
  await shots(page, "04-failed-and-scans");

  // The list as the api gives it: five documents, their states and their counts.
  const response = await context.request.get(LIST);
  expect(response.status()).toBe(200);
  const items = (
    (await response.json()) as {
      items: Array<{
        name: string;
        status: string;
        pages_text: number | null;
        pages_image: number | null;
      }>;
    }
  ).items;
  const states = Object.fromEntries(
    items.map((item) => [
      item.name,
      `${item.status} ${item.pages_text} ${item.pages_image}`,
    ]),
  );
  expect(states).toEqual({
    [CV]: "done 12 0",
    [RESTRICTED]: "done 2 0",
    [LOCKED]: "failed null null",
    [CUT]: "failed null null",
    [SCANNED]: "awaiting_transcription 3 2",
  });
});

test("no log holds a name or a word of a PDF", () => {
  const logs = compose("logs", "--no-color", "api", "worker", "parser");
  expect(logs.length).toBeGreaterThan(0);
  expect(logs).not.toContain(MARKER_NAME);
  expect(logs).not.toContain(MARKER_TEXT);
  expect(logs).not.toContain("billing service");
});

test("the journey left no error in the browser console", () => {
  expect(errors).toEqual([]);
});
