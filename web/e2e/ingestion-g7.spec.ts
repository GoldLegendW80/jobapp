import { execFileSync } from "node:child_process";

import { expect, test, type BrowserContext, type Page } from "@playwright/test";

import { makeDocx, makeZip, makeZipBomb } from "./ingestion-zip";
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
 * Task G7 of the ingestion module against the real stack through Caddy, no mocks: a Word
 * document and a LinkedIn like export are read by the worker through the parser service while
 * the page watches. The document goes from "Being read" to "Ready" and its preview shows the
 * link as "LinkedIn (https://...)"; the export becomes one row per CSV it holds and its own row
 * goes; a zip bomb says why it was not read. Then what the page cannot show: no log holds a
 * word of a document.
 * Every file is made up (a fictitious person), written by ./ingestion-zip.ts.
 *
 * Locally, on a scratch Compose project (its own volumes, torn down afterwards):
 *   HTTP_PORT=8097 docker compose -p jobapp-g7 -f compose.yml -f compose.mac.yml up -d --build --wait
 *   docker compose -p jobapp-g7 -f compose.yml exec -T api alembic upgrade head
 *   E2E_CADDY_URL=http://127.0.0.1:8097 E2E_COMPOSE_PROJECT=jobapp-g7 \
 *     pnpm --dir web exec playwright test -c e2e/ingestion-all.config.ts
 *   docker compose -p jobapp-g7 -f compose.yml down -v
 */
test.describe.configure({ mode: "serial" });
test.skip(!caddyUrl, "set E2E_CADDY_URL with the Compose stack running");

const run = Date.now().toString(36);
const project = process.env.E2E_COMPOSE_PROJECT;
const EMAIL = `e2e-ingestion-g7-${run}@example.org`;
const NAME = "Camille Example";
const PASSWORD = `ingestion archives ${run} passphrase`;
const LIST = "/api/ingestion/documents";
const MIB = 1024 * 1024;

// Words no log may hold: one in a file name, one in the text of each file.
const MARKER_NAME = `zq-zipname-${run}`;
const MARKER_TEXT = `zq-ziptext-${run}`;

const LINK = "https://www.example.org/in/camille-example";
const CV = `CV ${MARKER_NAME}.docx`;
const EXPORT = "LinkedIn export.zip";
const BOMB = "bomb.zip";
const POSITIONS = `${EXPORT}: Positions.csv`;
const PROFILE = `${EXPORT}: Profile.csv`;
const SKILLS = `${EXPORT}: Skills.csv`;

const cv = () =>
  makeDocx([
    { text: "Camille Example, data engineer" },
    { text: "Find me on ", link: { shown: "LinkedIn", address: LINK } },
    { text: `Reference ${MARKER_TEXT}.` },
  ]);
const exported = () =>
  makeZip([
    {
      name: "Connections.csv",
      content: "First Name,Last Name\r\nSomeone,Never-Read\r\n",
    },
    {
      name: "Positions.csv",
      content: `Company Name,Title,Started On\r\nExample Rail,Data engineer ${MARKER_TEXT},Mar 2021\r\n`,
    },
    {
      name: "Profile.csv",
      content:
        "First Name,Last Name,Headline\r\nCamille,Example,Data engineer\r\n",
    },
    { name: "Skills.csv", content: "Name\r\nPython\r\nSQL\r\n" },
    { name: "messages.csv", content: "FROM,CONTENT\r\nSomeone,never read\r\n" },
  ]);

const takeShots = makeShots("ingestion-g7", "ingestion-g7");

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

async function previewText(page: Page, name: string) {
  await row(page, name)
    .getByRole("button", { name: `Preview ${name}` })
    .click();
  const dialog = page.getByRole("dialog", { name });
  return {
    dialog,
    text: dialog.getByRole("region", { name: "Text of the document" }),
  };
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
  await page.goto("/profile/fill");
  // The page opens on step 1, the prompt (task G3): the documents are the step after it.
  await page.getByRole("button", { name: "Go on to your documents" }).click();
  await expect(
    page.getByRole("heading", { level: 2, name: "Step 2: Add your documents" }),
  ).toBeVisible();
});

test("a Word document and an export wait as Being read while the worker is away", async () => {
  // With the worker stopped the jobs stay in the queue: the state a user sees for a moment
  // on a busy server, held here for as long as the test needs to look at it.
  compose("stop", "worker");
  workerStopped = true;
  await picker(page).setInputFiles([file(CV, cv()), file(EXPORT, exported())]);
  for (const name of [CV, EXPORT]) {
    await expect(row(page, name)).toContainText("Being read", {
      timeout: 30_000,
    });
    // Since this task an archive has a reader: nothing says a later version will read it.
    await expect(row(page, name)).not.toContainText("This version cannot read");
    await expect(row(page, name)).toContainText("Word or ZIP");
  }
  await shots(page, "01-being-read");
});

test("both are read once the worker is back: the export as one row per CSV, its own row gone", async () => {
  test.setTimeout(180_000);
  compose("start", "worker");
  workerStopped = false;
  await expect(row(page, CV)).toContainText("Ready", { timeout: 120_000 });
  await expect(row(page, CV)).toContainText("Word");
  await expect(row(page, CV)).not.toContainText("Word or ZIP");
  for (const name of [POSITIONS, PROFILE, SKILLS]) {
    await expect(row(page, name)).toContainText("Ready", { timeout: 120_000 });
  }
  // The archive itself is no document any more, and what nobody reads never became one.
  await expect(documents(page).getByRole("listitem")).toHaveCount(4);
  await expect(documents(page)).not.toContainText("Connections.csv");
  await expect(documents(page)).not.toContainText("messages.csv");
  await shots(page, "02-read");
});

test("the previews show the text, and the link as LinkedIn (https://...)", async () => {
  const word = await previewText(page, CV);
  await expect(word.text).toContainText("Camille Example, data engineer");
  await expect(word.text).toContainText(`Find me on LinkedIn (${LINK})`);
  await shots(page, "03-preview-word", false);
  await page.keyboard.press("Escape");
  await expect(word.dialog).toHaveCount(0);

  const positions = await previewText(page, POSITIONS);
  await expect(positions.text).toContainText("Company Name,Title,Started On");
  await expect(positions.text).toContainText("Example Rail,Data engineer");
  await shots(page, "04-preview-csv", false);
  await page.keyboard.press("Escape");
  await expect(positions.dialog).toHaveCount(0);
});

test("the same export added again makes no second copy", async () => {
  test.setTimeout(120_000);
  const [stored] = await Promise.all([
    page.waitForResponse(
      (response) =>
        response.request().method() === "POST" &&
        new URL(response.url()).pathname === LIST,
    ),
    picker(page).setInputFiles([file("the same again.zip", exported())]),
  ]);
  // The archive's own row went with the first reading, so this is a new upload.
  expect(stored.status()).toBe(201);
  const again = ((await stored.json()) as { id: string }).id;
  // Its row is read and goes: the three files are the ones already there.
  await expect
    .poll(
      async () => {
        const list = (await (await page.request.get(LIST)).json()) as {
          items: { id: string }[];
        };
        return list.items.map((item) => item.id);
      },
      { timeout: 90_000 },
    )
    .not.toContain(again);
  await expect(row(page, "the same again.zip")).toHaveCount(0, {
    timeout: 30_000,
  });
  await expect(documents(page).getByRole("listitem")).toHaveCount(4);
  await expect(row(page, POSITIONS)).toHaveCount(1);
});

test("a zip bomb says why it was not read", async () => {
  test.setTimeout(120_000);
  await picker(page).setInputFiles([file(BOMB, makeZipBomb())]);
  const bomb = row(page, BOMB);
  await expect(bomb).toContainText("Could not be read", { timeout: 90_000 });
  await expect(bomb).toContainText(
    "This file unpacks to more than a file may hold here. Save it again from the application that made it, and add the new file.",
  );
  await expect(bomb.getByRole("button", { name: /^Preview/ })).toHaveCount(0);
  await shots(page, "05-bomb");
  // The parser took it and still answers: a file added after it is read.
  await picker(page).setInputFiles([
    file("after the bomb.docx", makeDocx([{ text: "Read after the bomb." }])),
  ]);
  await expect(row(page, "after the bomb.docx")).toContainText("Ready", {
    timeout: 90_000,
  });
});

test("no log holds a word of a document or the name of a file, and the page raised no error", async () => {
  // The four documents, the bomb and the file read after it.
  await expect(documents(page).getByRole("listitem")).toHaveCount(6);
  const logs = compose(
    "logs",
    "--no-color",
    "api",
    "worker",
    "parser",
    "caddy",
  );
  expect(logs).toContain("document parsed");
  expect(logs).toContain("export unpacked");
  for (const secret of [MARKER_NAME, MARKER_TEXT, "Example Rail", "Never-Read"])
    expect(logs).not.toContain(secret);
  expect(errors).toEqual([]);
});
