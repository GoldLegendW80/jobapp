import { execFileSync } from "node:child_process";

import { expect, test, type BrowserContext, type Page } from "@playwright/test";

import { makeBlankPng, makeScanPdf, makeScreenshot } from "./ingestion-image";
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
 * Task G8 of the ingestion module against the real stack through Caddy, no mocks: a screenshot
 * and a scanned PDF are turned into page images by the worker through the parser service while
 * the page watches. Each row turns to "will be read by the agent at the next run", the preview
 * shows the stored page images and the row their count; a PDF that the earlier reader had left
 * with a note about its scanned page is read when it is added again; an image of 26 megapixels
 * says why it was not read. Then what the page cannot show: no log holds a file name.
 *
 * Every picture and every PDF is built in code (ingestion-image.ts): nothing here was ever a
 * real document.
 *
 * Locally, on a scratch Compose project (its own volumes and image tags, torn down afterwards):
 *   E2E_CADDY_URL=http://127.0.0.1:<port> E2E_COMPOSE_PROJECT=jobapp-g8 \
 *     pnpm --dir web exec playwright test -c e2e/ingestion-all.config.ts ingestion-g8 --workers=1
 */
test.describe.configure({ mode: "serial" });
test.skip(!caddyUrl, "set E2E_CADDY_URL with the Compose stack running");

const run = Date.now().toString(36);
const project = process.env.E2E_COMPOSE_PROJECT;
const EMAIL = `e2e-ingestion-g8-${run}@example.org`;
const NAME = "Camille Example";
const PASSWORD = `ingestion images ${run} passphrase`;
const MIB = 1024 * 1024;

// A word no log may hold: it is in every file name of this journey.
const MARKER = `zq-imgname-${run}`;
const SHOT = `CV fragment ${MARKER}.png`;
const SCAN = `Scanned CV ${MARKER}.pdf`;
const LEFT = `Left by the earlier reader ${MARKER}.pdf`;
const POSTER = `Poster ${MARKER}.png`;
const OLD_NOTE = "1 page holds no text and waits for the image reader.";
const NEXT_RUN = "will be read by the agent at the next run";

const mixed = () =>
  makeScanPdf([
    Array.from(
      { length: 8 },
      (_, n) =>
        `Line ${String(n + 1)} of a page of text that the reader keeps as text.`,
    ),
    "scan",
  ]);

const takeShots = makeShots("ingestion-g8", "ingestion-g8");

async function shots(page: Page, name: string, fullPage = true) {
  await expect(page.locator("[data-sonner-toast]")).toHaveCount(0, {
    timeout: 15_000,
  });
  await takeShots(page, name, fullPage);
}

const file = (name: string, buffer: Buffer) => ({
  name,
  mimeType: "application/octet-stream",
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

/** One statement in the database of the stack, as the migration's owner. */
function sql(statement: string): string {
  return compose(
    "exec",
    "-T",
    "db",
    "sh",
    "-c",
    'psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "$0"',
    statement,
  );
}

async function openPreview(page: Page, name: string) {
  await row(page, name)
    .getByRole("button", { name: `Preview ${name}` })
    .click();
  return page.getByRole("dialog", { name });
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
  await page.goto("/profile/fill?step=documents");
  await expect(
    page.getByRole("heading", { level: 2, name: "Step 2: Add your documents" }),
  ).toBeVisible();
});

test("a screenshot and a scanned PDF turn to: will be read by the agent at the next run", async () => {
  test.setTimeout(180_000);
  await picker(page).setInputFiles([
    file(SHOT, makeScreenshot()),
    file(SCAN, makeScanPdf(["scan", "scan"])),
  ]);
  for (const name of [SHOT, SCAN]) {
    await expect(row(page, name)).toContainText("Waiting for the agent", {
      timeout: 120_000,
    });
    await expect(row(page, name)).toContainText(NEXT_RUN);
    // An image has a reader since this task: nothing says a later version will read it.
    await expect(row(page, name)).not.toContainText("This version cannot read");
  }
  await expect(row(page, SHOT)).toContainText("1 page kept as an image");
  await expect(row(page, SCAN)).toContainText("2 pages kept as images");
  await shots(page, "01-waiting-for-the-agent");
});

test("the preview shows the stored page images, served as JPEG to their owner", async () => {
  const dialog = await openPreview(page, SCAN);
  const images = dialog.getByRole("img");
  await expect(images).toHaveCount(2);
  for (const number of [1, 2]) {
    const image = dialog.getByRole("img", {
      name: `Page ${String(number)}, kept as an image`,
    });
    await image.scrollIntoViewIfNeeded();
    // An A4 page rendered with its longest side at 1568 pixels.
    await expect
      .poll(() =>
        image.evaluate((element: HTMLImageElement) => element.naturalHeight),
      )
      .toBe(1568);
  }
  await expect(dialog).toContainText(
    "This page is an image. The agent will read it at the next run.",
  );
  await shots(page, "02-preview-scan", false);
  const address = await images.first().getAttribute("src");
  expect(address).toMatch(
    /^\/api\/ingestion\/documents\/[0-9a-f-]{36}\/images\/0$/,
  );
  const answer = await page.request.get(address ?? "");
  expect(answer.status()).toBe(200);
  expect(answer.headers()["content-type"]).toBe("image/jpeg");
  expect(answer.headers()["x-content-type-options"]).toBe("nosniff");
  expect(answer.headers()["cache-control"]).toBe("no-store");
  expect((await answer.body()).subarray(0, 3)).toEqual(
    Buffer.from([0xff, 0xd8, 0xff]),
  );
  // A page that holds no image is not found, and so is the image for somebody not signed in.
  expect(
    (await page.request.get((address ?? "").replace(/0$/, "7"))).status(),
  ).toBe(404);
  const stranger = await page.request.fetch(address ?? "", {
    headers: { cookie: "" },
    maxRedirects: 0,
  });
  expect([401, 404]).toContain(stranger.status());
  await page.keyboard.press("Escape");
  await expect(page.getByRole("dialog")).toHaveCount(0);

  const shot = await openPreview(page, SHOT);
  const only = shot.getByRole("img", { name: "Page 1, kept as an image" });
  await expect
    .poll(() =>
      only.evaluate((element: HTMLImageElement) => element.naturalWidth),
    )
    .toBe(800);
  await shots(page, "03-preview-screenshot", false);
  await page.keyboard.press("Escape");
  await expect(page.getByRole("dialog")).toHaveCount(0);
});

test("a PDF the earlier reader left with a note is read when it is added again", async () => {
  test.setTimeout(240_000);
  // The row as the reader of task G2b left it: its text page read, its scanned page named in
  // a note, written by version 1 of the parser. Made here from an upload the worker never saw.
  compose("stop", "worker");
  workerStopped = true;
  await picker(page).setInputFiles([file(LEFT, mixed())]);
  await expect(row(page, LEFT)).toContainText("Being read", {
    timeout: 30_000,
  });
  const changed = sql(
    "UPDATE ingestion_documents SET text_status = 'done', bytes = NULL, " +
      "text = 'A page of text that the earlier reader kept.' || chr(12), " +
      `note = '${OLD_NOTE}', truncated = true, parser_version = 1, pages = 2, ` +
      "pages_text = 1, pages_image = 1, pending_since = NULL " +
      `WHERE name = '${LEFT}'`,
  );
  expect(changed).toContain("UPDATE 1");
  compose("start", "worker");
  workerStopped = false;
  await page.reload();
  await expect(row(page, LEFT)).toContainText("Ready", { timeout: 60_000 });
  await expect(row(page, LEFT)).toContainText(OLD_NOTE);
  await shots(page, "04-left-by-the-earlier-reader");

  // The same file again: this version reads more than the one that left the note.
  await picker(page).setInputFiles([file(LEFT, mixed())]);
  await expect(row(page, LEFT)).toContainText("Waiting for the agent", {
    timeout: 120_000,
  });
  await expect(row(page, LEFT)).toContainText(NEXT_RUN);
  await expect(row(page, LEFT)).toContainText(
    "1 page read as text, 1 as an image",
  );
  await expect(row(page, LEFT)).not.toContainText(OLD_NOTE);
  await expect(row(page, LEFT)).toHaveCount(1);
  const dialog = await openPreview(page, LEFT);
  await expect(dialog).toContainText("Line 1 of a page of text");
  await expect(
    dialog.getByRole("img", { name: "Page 2, kept as an image" }),
  ).toBeVisible();
  await expect(dialog.getByRole("img")).toHaveCount(1);
  await shots(page, "05-read-again", false);
  await page.keyboard.press("Escape");
  await expect(page.getByRole("dialog")).toHaveCount(0);
});

test("an image of 26 megapixels says why it was not read", async () => {
  test.setTimeout(180_000);
  await picker(page).setInputFiles([file(POSTER, makeBlankPng(5200, 5000))]);
  await expect(row(page, POSTER)).toContainText("Could not be read", {
    timeout: 120_000,
  });
  await expect(row(page, POSTER)).toContainText(
    "This image is too large: more than 25 megapixels",
  );
  await shots(page, "06-too-many-pixels");
});

test("no log holds a file name, and the page raised no error", () => {
  const logs = compose(
    "logs",
    "--no-color",
    "api",
    "worker",
    "parser",
    "caddy",
  );
  expect(logs).toContain("document rendered");
  expect(logs).not.toContain(MARKER);
  expect(logs).not.toContain("JFIF");
  expect(errors).toEqual([]);
});
