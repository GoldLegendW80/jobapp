import { execFileSync, spawn, spawnSync } from "node:child_process";
import { randomBytes } from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import { crc32, deflateSync } from "node:zlib";

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

const takeShots = makeShots("ingestion-g1", "ingestion-g1");

/**
 * Screenshots once every toast is gone, so none covers the page. A full page is taken from its
 * top with the header in the flow: a sticky header is painted where the page was scrolled to,
 * across whatever is there.
 */
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

/**
 * Task G1 of the ingestion module against the real stack through Caddy, no mocks: the
 * /profile/fill page, adding a text file, a PDF and an image, pasting a text, the list and its
 * states, the preview, a refused type, a file that is too large, a duplicate, an upload stopped
 * on its way, the secret warning, deleting with the dialog, a second user who sees nothing; then
 * what the page cannot
 * show: the 20 MiB limit on the upload route alone, the api's memory with two uploads at once,
 * and logs that hold nothing of a document. Every document is made up (a fictitious person).
 *
 * Locally, on a scratch Compose project (its own volumes, torn down afterwards):
 *   HTTP_PORT=8097 docker compose -p jobapp-g1 -f compose.yml -f compose.mac.yml up -d --build --wait
 *   docker compose -p jobapp-g1 -f compose.yml exec -T api alembic upgrade head
 *   E2E_CADDY_URL=http://127.0.0.1:8097 E2E_COMPOSE_PROJECT=jobapp-g1 \
 *     pnpm --dir web exec playwright test -c e2e/ingestion-all.config.ts
 *   docker compose -p jobapp-g1 -f compose.yml down -v
 */
test.describe.configure({ mode: "serial" });
test.skip(!caddyUrl, "set E2E_CADDY_URL with the Compose stack running");

const run = Date.now().toString(36);
const EMAIL = `e2e-ingestion-${run}@example.org`;
const OTHER_EMAIL = `e2e-ingestion-other-${run}@example.org`;
const NAME = "Robin Example";
const OTHER_NAME = "Sasha Other";
const PASSWORD = `ingestion journey ${run} passphrase`;
const project = process.env.E2E_COMPOSE_PROJECT;

const MIB = 1024 * 1024;
const FILE_MAX = 20 * MIB;
const UPLOAD = "/api/ingestion/documents";
const DELETE_PROMISE =
  "Gone from the app now. The server's backups still hold a copy for a while, until newer backups replace them.";
const STOPPED = "stopped on its way.pdf";
// Words that must never reach a log, unique to this run.
const MARKER_NAME = `zq-name-${run}`;
const MARKER_TEXT = `zq-text-${run}`;

const DOSSIER = [
  "# Robin Example",
  "",
  "Applied AI engineer in Nantes. robin@example.org",
  "",
  "## Experience",
  "",
  "- Acme Robotics, AI engineer, since March 2024.",
  "- Globex, developer, 2021 to 2024.",
  "",
  "## What I built",
  "",
  "A retrieval assistant for support agents, and the evaluation suite that gates its releases.",
  "",
].join("\n");
const SKILLS = JSON.stringify(
  { name: "Robin Example", skills: ["Python", "TypeScript", "PostgreSQL"] },
  null,
  2,
);
const LETTER =
  "Dear hiring team,\n\nI build agents that run real operations.\n\nRobin Example\n";
const ABOUT =
  "About\n\nApplied AI engineer. I like turning prototypes into products people use.";
const LONG = Array.from(
  { length: 900 },
  (_, n) =>
    `Line ${String(n + 1).padStart(3, "0")} of the project notes of Robin Example.`,
).join("\n");
// The shape of an AWS access key id, assembled here so no scanner takes this file for a leak.
const KEY_LIKE = ["AKIA", "Z7XQ2M4K", "T6WB3N5P"].join("");
const ENV_NOTES = `Notes on the deployment\n\nexport AWS_ACCESS_KEY_ID=${KEY_LIKE}\n`;

/** A real one page PDF with its cross reference table. Since G2b the parser reads it. */
function tinyPdf(line: string): Buffer {
  const stream = `BT /F1 12 Tf 72 760 Td (${line}) Tj ET`;
  const objects = [
    "<< /Type /Catalog /Pages 2 0 R >>",
    "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
    "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
    `<< /Length ${stream.length} >>\nstream\n${stream}\nendstream`,
    "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
  ];
  let body = "%PDF-1.4\n";
  const offsets: number[] = [];
  objects.forEach((object, index) => {
    offsets.push(body.length);
    body += `${index + 1} 0 obj\n${object}\nendobj\n`;
  });
  const xref = body.length;
  body += `xref\n0 ${objects.length + 1}\n0000000000 65535 f \n`;
  for (const offset of offsets)
    body += `${String(offset).padStart(10, "0")} 00000 n \n`;
  body += `trailer\n<< /Size ${objects.length + 1} /Root 1 0 R >>\nstartxref\n${xref}\n%%EOF\n`;
  return Buffer.from(body, "latin1");
}

/** A real PNG (a colour gradient), standing in for a screenshot. */
function gradientPng(width = 320, height = 200): Buffer {
  const rows: Buffer[] = [];
  for (let y = 0; y < height; y += 1) {
    const row = Buffer.alloc(1 + width * 3);
    for (let x = 0; x < width; x += 1) {
      row[1 + x * 3] = Math.round((255 * x) / width);
      row[2 + x * 3] = Math.round((255 * y) / height);
      row[3 + x * 3] = 160;
    }
    rows.push(row);
  }
  const chunk = (type: string, data: Buffer) => {
    const length = Buffer.alloc(4);
    length.writeUInt32BE(data.length);
    const content = Buffer.concat([Buffer.from(type, "ascii"), data]);
    const crc = Buffer.alloc(4);
    crc.writeUInt32BE(crc32(content));
    return Buffer.concat([length, content, crc]);
  };
  const header = Buffer.alloc(13);
  header.writeUInt32BE(width, 0);
  header.writeUInt32BE(height, 4);
  header.writeUInt8(8, 8);
  header.writeUInt8(2, 9);
  return Buffer.concat([
    Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]),
    chunk("IHDR", header),
    chunk("IDAT", deflateSync(Buffer.concat(rows))),
    chunk("IEND", Buffer.alloc(0)),
  ]);
}

/** `size` bytes that start as a PDF does and compress to nothing less: a worst case to store. */
const bigPdf = (size: number) =>
  Buffer.concat([Buffer.from("%PDF-1.4\n"), randomBytes(size - 9)]);

/** Characters as the api counts them. */
const characters = (text: string) => Array.from(text).length;

const file = (name: string, content: string | Buffer, mimeType = "") => ({
  name,
  mimeType,
  buffer: typeof content === "string" ? Buffer.from(content, "utf8") : content,
});

const documents = (page: Page) =>
  page.getByRole("list", { name: "Your documents" });
const row = (page: Page, name: string) =>
  documents(page).getByRole("listitem").filter({ hasText: name });
const queue = (page: Page) =>
  page.getByRole("list", { name: "Files being added" });
const picker = (page: Page) => page.getByTestId("document-files");

async function add(page: Page, ...files: ReturnType<typeof file>[]) {
  await picker(page).setInputFiles(files);
}

interface Listed {
  id: string;
  name: string;
  status: string;
  size: number;
}

async function listed(context: BrowserContext): Promise<Listed[]> {
  const response = await context.request.get(UPLOAD);
  expect(response.status()).toBe(200);
  return ((await response.json()) as { items: Listed[] }).items;
}

async function sessionCookie(context: BrowserContext): Promise<string> {
  const cookie = (await context.cookies()).find(
    (candidate) => candidate.name === "jobapp_session",
  );
  if (!cookie) throw new Error("no session cookie");
  return `jobapp_session=${cookie.value}`;
}

interface Answer {
  status: number;
  type: string;
  body: string;
}

/**
 * A request with curl, which reads an answer that arrives while it is still sending (a refusal
 * of a large body), where an HTTP library gives up on the broken pipe.
 */
function curlArgs(target: string, cookie: string, extra: string[]): string[] {
  return [
    "-s",
    "--path-as-is",
    "--max-time",
    "120",
    "-o",
    "-",
    "-w",
    "\n%{http_code} %{content_type}",
    "-H",
    `Cookie: ${cookie}`,
    "-H",
    "X-Requested-With: jobapp",
    "-H",
    "Content-Type: application/octet-stream",
    "-H",
    "Expect:",
    ...extra,
    "--data-binary",
    "@-",
    `${caddyUrl ?? ""}${target}`,
  ];
}

function parse(output: string): Answer {
  const cut = output.lastIndexOf("\n");
  const [status = "0", ...type] = output.slice(cut + 1).split(" ");
  return {
    status: Number(status),
    type: type.join(" "),
    body: output.slice(0, Math.max(cut, 0)),
  };
}

function curl(
  target: string,
  cookie: string,
  body: Buffer,
  extra: string[] = [],
): Answer {
  const result = spawnSync("curl", curlArgs(target, cookie, extra), {
    input: body,
    encoding: "utf8",
    maxBuffer: 16 * MIB,
    // curl gives up by itself after 120 s (--max-time). This bound is for a curl that never
    // gets that far: the journey then fails here, with the request named, instead of hanging.
    timeout: 150_000,
    killSignal: "SIGKILL",
  });
  if (result.error)
    throw new Error(
      `curl ${extra.join(" ")} ${target}: ${result.error.message}`,
    );
  return parse(result.stdout);
}

function curlAsync(
  target: string,
  cookie: string,
  body: Buffer,
): Promise<Answer> {
  return new Promise((resolve, reject) => {
    const child = spawn("curl", curlArgs(target, cookie, []));
    let output = "";
    child.stdout.on("data", (data: Buffer) => {
      output += data.toString("utf8");
    });
    child.on("error", reject);
    child.on("close", () => {
      resolve(parse(output));
    });
    child.stdin.on("error", () => {}); // the server may answer before the body is through
    child.stdin.end(body);
  });
}

const CHUNKED = ["-H", "Transfer-Encoding: chunked"];
/** Caddy's own refusal is plain text; the api's is JSON with a sentence. */
const fromCaddy = (answer: Answer) =>
  answer.status === 413 && !answer.type.includes("json");

function compose(...args: string[]): string {
  if (!project) throw new Error("E2E_COMPOSE_PROJECT is not set");
  return execFileSync(
    "docker",
    ["compose", "-p", project, "-f", "compose.yml", ...args],
    { cwd: root, encoding: "utf8", maxBuffer: 256 * MIB },
  );
}

let context: BrowserContext;
let page: Page;
let errors: string[];
let cookie: string;
// The second user, from the test that creates them to the end of the journey.
let secondUser: BrowserContext | undefined;
let secondCookie = "";

test.beforeAll(async ({ browser }) => {
  context = await newContext(browser);
  page = await context.newPage();
  errors = watchErrors(page, [
    [415, /\/api\/ingestion\/documents$/],
    [404, /\/api\/ingestion\/documents\//],
  ]);
});

test.afterAll(async () => {
  await secondUser?.close();
  await context.close();
  clearAttempts([EMAIL, OTHER_EMAIL]);
});

test("a new user follows Fill my profile with an agent from the profile", async () => {
  await page.goto(cliLink(EMAIL));
  await setPassword(page, PASSWORD, NAME);
  cookie = await sessionCookie(context);
  await page
    .getByRole("navigation", { name: "Main" })
    .getByRole("link", { name: "Profile" })
    .click();
  await page
    .getByRole("link", { name: "Fill my profile with an agent" })
    .click();
  await expect(page).toHaveURL(/\/profile\/fill$/);
  await expect(
    page.getByRole("heading", {
      level: 1,
      name: "Fill my profile with an agent",
    }),
  ).toBeVisible();
  await expect(page).toHaveTitle("Fill my profile \u00b7 jobapp");

  // Three steps, each one a control since task G11 built the run of the third.
  const steps = page.getByRole("navigation", { name: "Steps" });
  await expect(steps.getByRole("listitem")).toHaveText([
    /Prepare(?!\s*Soon)/,
    /Documents(?!\s*Soon)/,
    /Review(?!\s*Soon)/,
  ]);
  await expect(steps.getByRole("button")).toHaveCount(3);
  // The page opens on step 1, the prompt (task G3, ingestion-g3.spec.ts): on to the documents.
  await expect(
    steps.getByRole("button", { name: /Step 1: Prepare/ }),
  ).toHaveAttribute("aria-current", "step");
  await steps.getByRole("button", { name: /Step 2: Documents/ }).click();
  await expect(
    steps.getByRole("button", { name: /Step 2: Documents/ }),
  ).toHaveAttribute("aria-current", "step");
  await expect(
    page.getByRole("heading", { level: 2, name: "Step 2: Add your documents" }),
  ).toBeVisible();

  await expect(
    page.getByText(
      "Your documents stay on this server, and no other user of this app can see them. Nothing is sent to an AI model until you start a run, and the app asks you first.",
    ),
  ).toBeVisible();
  await expect(
    page.getByText("No document yet. Add a file or paste a text above."),
  ).toBeVisible();
  await expect(
    page
      .getByRole("navigation", { name: "Main" })
      .getByRole("link", { name: "Profile" }),
  ).toHaveAttribute("aria-current", "page");
  await shots(page, "01-empty");
});

test("text files are read at once: chosen by keyboard, then dropped", async () => {
  // The system's picker opens from the button with the keyboard alone.
  const choose = page.getByRole("button", { name: "Choose files" });
  await choose.focus();
  const chooser = page.waitForEvent("filechooser");
  await page.keyboard.press("Enter");
  await (
    await chooser
  ).setFiles([
    file("dossier.md", DOSSIER, "text/markdown"),
    file("skills.json", SKILLS, "application/json"),
  ]);
  await expect(row(page, "dossier.md")).toContainText("Ready");
  await expect(row(page, "skills.json")).toContainText("Ready");
  await expect(row(page, "dossier.md")).toContainText(/Text, \d+ bytes, added/);
  // The result is announced politely to a screen reader.
  await expect(
    page.getByRole("status").filter({ hasText: "skills.json added." }),
  ).toHaveCount(1);
  await expect(queue(page)).toHaveCount(0);

  // A file dropped on the zone.
  await page
    .getByRole("heading", { name: "Drop files here, or choose them" })
    .evaluate((heading, content) => {
      const transfer = new DataTransfer();
      transfer.items.add(
        new File([content], "cover letter.txt", { type: "text/plain" }),
      );
      const zone = heading.parentElement;
      if (!zone) throw new Error("no drop zone");
      for (const type of ["dragover", "drop"])
        zone.dispatchEvent(
          new DragEvent(type, {
            dataTransfer: transfer,
            bubbles: true,
            cancelable: true,
          }),
        );
    }, LETTER);
  await expect(row(page, "cover letter.txt")).toContainText("Ready");
  await expect(documents(page).getByRole("listitem")).toHaveCount(3);
});

test("a PDF is read, and an image is kept as a page image: the page says the agent will read it", async () => {
  await add(
    page,
    file(
      "CV Robin Example.pdf",
      // Long enough to be a page of text: a page with a few characters only counts as a scan.
      tinyPdf(
        "Robin Example, applied AI engineer, has shipped agents to production since 2023",
      ),
      "application/pdf",
    ),
    file("screenshot.png", gradientPng(), "image/png"),
  );
  const cv = row(page, "CV Robin Example.pdf");
  // Since G2b the worker reads a PDF: the row turns to Ready by itself (the list is watched).
  await expect(cv).toContainText("Ready", { timeout: 60_000 });
  await expect(cv).toContainText("1 page read as text");
  await expect(cv).toContainText(/PDF, \d+ bytes/);
  // Since G8 an image has a reader too: the worker normalises it through the parser service and
  // the row says in plain words who reads it next. Until G8 it said "Waiting" and "This version
  // cannot read images yet."; no kind that is kept waits for a later version any more (a GIF or
  // a WebP is refused at the door: "a refused type..." below).
  const image = row(page, "screenshot.png");
  await expect(image).toContainText("Waiting for the agent", {
    timeout: 60_000,
  });
  await expect(image).toContainText(
    "This image will be read by the agent at the next run.",
  );
  await expect(image).toContainText("1 page kept as an image");
  await expect(image).not.toContainText("This version cannot read");
  // The PDF has its text to preview and the image its page image; both can be deleted.
  await expect(cv.getByRole("button", { name: /^Preview/ })).toHaveCount(1);
  await expect(image.getByRole("button", { name: /^Preview/ })).toHaveCount(1);
  await expect(
    cv.getByRole("button", { name: "Delete CV Robin Example.pdf" }),
  ).toBeVisible();
  await expect(
    image.getByRole("button", { name: "Delete screenshot.png" }),
  ).toBeVisible();

  // The type came from the bytes: what the browser declared changed nothing.
  const kinds = Object.fromEntries(
    (
      (await (await context.request.get(UPLOAD)).json()) as {
        items: Array<{ name: string; kind: string; mime: string }>;
      }
    ).items.map((item) => [item.name, `${item.kind} ${item.mime}`]),
  );
  expect(kinds["CV Robin Example.pdf"]).toBe("pdf application/pdf");
  expect(kinds["screenshot.png"]).toBe("image image/png");
  expect(kinds["skills.json"]).toBe("text text/plain");
});

test("a pasted text becomes a document", async () => {
  const box = page.getByLabel("Or paste a text");
  const addText = page.getByRole("button", { name: "Add this text" });
  await expect(addText).toBeDisabled();
  await box.fill(ABOUT);
  await expect(
    page.getByText(`${characters(ABOUT)} of 100,000 characters`),
  ).toBeVisible();
  await addText.click();
  await expect(page.getByText("Text added.", { exact: true })).toBeVisible();
  await expect(box).toHaveValue("");
  await expect(row(page, "Pasted text")).toContainText("Ready");
  await expect(row(page, "Pasted text")).toContainText(
    /Pasted text, \d+ bytes/,
  );

  // Over the limit: nothing is sent, and the page says what to do instead.
  await box.fill("a".repeat(100_001));
  await expect(
    page.getByText(
      "Too long: 100,001 of 100,000 characters. Save it as a text file and add the file instead.",
    ),
  ).toBeVisible();
  await expect(addText).toBeDisabled();
  await box.fill("");
});

test("the preview shows the text kept, a part at a time", async () => {
  await add(page, file("project notes.txt", LONG, "text/plain"));
  await expect(row(page, "project notes.txt")).toContainText("Ready");
  await row(page, "dossier.md")
    .getByRole("button", { name: "Preview dossier.md" })
    .click();
  const dialog = page.getByRole("dialog", { name: "dossier.md" });
  await expect(dialog).toBeVisible();
  await expect(
    dialog.getByRole("region", { name: "Text of the document" }),
  ).toContainText("Acme Robotics, AI engineer, since March 2024.");
  await expect(
    dialog.getByText(`${characters(DOSSIER)} characters`),
  ).toBeVisible();
  await shots(page, "02-preview", false);
  await page.keyboard.press("Escape");
  await expect(dialog).toHaveCount(0);

  // A long text comes 20,000 characters at a time.
  await row(page, "project notes.txt")
    .getByRole("button", { name: "Preview project notes.txt" })
    .click();
  const long = page.getByRole("dialog", { name: "project notes.txt" });
  const total = characters(LONG).toLocaleString("en");
  await expect(
    long.getByText(`The first 20,000 of ${total} characters`),
  ).toBeVisible();
  const region = long.getByRole("region", { name: "Text of the document" });
  await expect(region).toContainText("Line 001 of the project notes");
  await expect(region).not.toContainText("Line 900 of the project notes");
  // Each "Show more" brings the next 20,000 characters, until the whole text is there.
  const more = long.getByRole("button", { name: "Show more" });
  const parts = Math.ceil(characters(LONG) / 20_000);
  for (let shown = 2; shown < parts; shown += 1) {
    await more.click();
    await expect(
      long.getByText(
        `The first ${(shown * 20_000).toLocaleString("en")} of ${total} characters`,
      ),
    ).toBeVisible();
  }
  await more.click();
  await expect(more).toHaveCount(0);
  await expect(region).toContainText("Line 900 of the project notes");
  await expect(long.getByText(`${total} characters`)).toBeVisible();
  await long.getByRole("button", { name: "Close" }).click();
  await expect(long).toHaveCount(0);
});

test("a text that looks like it holds a key asks to be checked, kept or deleted", async () => {
  await add(page, file("deployment notes.txt", ENV_NOTES, "text/plain"));
  const notes = row(page, "deployment notes.txt");
  const warning = notes.getByRole("group", {
    name: "Possible secret in deployment notes.txt",
  });
  await expect(warning).toContainText(
    "This may hold a password or an access key",
  );
  await expect(warning).toContainText(
    "No agent reads this document until you say it is fine.",
  );
  await expect(
    page.getByRole("status").filter({
      hasText:
        "deployment notes.txt added. It looks like it holds a password or a key",
    }),
  ).toHaveCount(1);
  // Its text is there, but it is not ready for an agent until the user has looked.
  await expect(notes).toContainText("To check");
  await shots(page, "03-documents");
  await warning.getByRole("button", { name: "It is fine, keep it" }).click();
  await expect(warning).toHaveCount(0);
  await expect(notes).toContainText("Ready");
  // Kept after a reload.
  await page.reload();
  await expect(row(page, "deployment notes.txt")).toContainText("Ready");
  await expect(
    page.getByRole("group", { name: /Possible secret/ }),
  ).toHaveCount(0);
});

test("a refused type, a file that is too large and a duplicate each say why", async () => {
  const before = (await listed(context)).length;
  const sent: string[] = [];
  const listener = (request: { method(): string; url(): string }) => {
    if (request.method() === "POST" && request.url().endsWith(UPLOAD))
      sent.push(request.url());
  };
  page.on("request", listener);

  await add(page, file("animation.gif", Buffer.from("GIF89a made up bytes")));
  const refused = queue(page)
    .getByRole("listitem")
    .filter({ hasText: "animation.gif" });
  await expect(refused).toContainText(
    "GIF images cannot be read. Convert the image to PNG or JPEG and add it again.",
  );
  // Said once to a screen reader: by the polite region, with no alert beside it.
  await expect(
    page.getByRole("status").filter({
      hasText: "animation.gif was not added. GIF images cannot be read.",
    }),
  ).toHaveCount(1);
  await expect(page.getByRole("alert")).toHaveCount(0);

  // One byte over the limit: refused in the page, never sent.
  await add(page, file("too large.pdf", bigPdf(FILE_MAX + 1)));
  const large = queue(page)
    .getByRole("listitem")
    .filter({ hasText: "too large.pdf" });
  await expect(large).toContainText("The file is larger than 20 MB.");

  // The same bytes under another name: one document, and the page says which.
  await add(page, file("dossier copy.md", DOSSIER));
  const copy = queue(page)
    .getByRole("listitem")
    .filter({ hasText: "dossier copy.md" });
  await expect(copy).toContainText(
    "The same file is already in your documents, as dossier.md.",
  );
  page.off("request", listener);
  expect(sent).toHaveLength(2); // the GIF and the copy; the large file never left
  expect(await listed(context)).toHaveLength(before);
  await expect(documents(page).getByRole("listitem")).toHaveCount(before);
  await shots(page, "04-refused-and-duplicate");

  // Dismissed by keyboard: the focus follows to the next notice, then to the button that
  // adds files, and is never lost with the row.
  const dismiss = (name: string) =>
    page.getByRole("button", { name: `Dismiss ${name}` });
  await dismiss("animation.gif").focus();
  await page.keyboard.press("Enter");
  await expect(dismiss("too large.pdf")).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(dismiss("dossier copy.md")).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(queue(page)).toHaveCount(0);
  await expect(
    page.getByRole("button", { name: "Choose files" }),
  ).toBeFocused();
});

test("deleting asks first, says what it means, and the document is gone for good", async () => {
  const before = await listed(context);
  const target = before.find((item) => item.name === "cover letter.txt");
  expect(target).toBeDefined();
  await row(page, "cover letter.txt")
    .getByRole("button", { name: "Delete cover letter.txt" })
    .click();
  const dialog = page.getByRole("alertdialog", {
    name: "Delete this document?",
  });
  await expect(dialog).toContainText("cover letter.txt");
  await expect(dialog).toContainText(
    "will be deleted for good, with its text.",
  );
  await expect(dialog).toContainText(DELETE_PROMISE);
  await shots(page, "05-delete", false);

  // Keep it: nothing happens.
  await dialog.getByRole("button", { name: "Keep it" }).click();
  await expect(dialog).toHaveCount(0);
  expect(await listed(context)).toHaveLength(before.length);

  await row(page, "cover letter.txt")
    .getByRole("button", { name: "Delete cover letter.txt" })
    .click();
  await dialog.getByRole("button", { name: "Delete for good" }).click();
  await expect(dialog).toHaveCount(0);
  await expect(page.getByText("Document deleted.")).toBeVisible();
  await expect(row(page, "cover letter.txt")).toHaveCount(0);
  // The row's button is gone: the focus is on the list, not lost.
  await expect(
    page.getByRole("heading", { level: 3, name: "Your documents" }),
  ).toBeFocused();

  const after = await listed(context);
  expect(after.map((item) => item.name)).not.toContain("cover letter.txt");
  expect(after).toHaveLength(before.length - 1);
  const gone = await context.request.get(`${UPLOAD}/${target?.id ?? ""}/text`);
  expect(gone.status()).toBe(404);

  // The list as it was left, after a reload.
  await page.reload();
  await expect(documents(page).getByRole("listitem")).toHaveCount(after.length);
  await expect(row(page, "dossier.md")).toContainText("Ready");
  await expect(row(page, "CV Robin Example.pdf")).toContainText("Ready");
  await expect(
    page.getByText(/^\d+ documents, .+ of 100 MB used$/),
  ).toBeVisible();
});

test("a second user sees nothing and cannot reach the first user's documents", async ({
  browser,
}) => {
  const mine = await listed(context);
  const dossier = mine.find((item) => item.name === "dossier.md");
  expect(dossier).toBeDefined();
  const id = dossier?.id ?? "";

  const other = await newContext(browser);
  secondUser = other;
  const otherPage = await other.newPage();
  try {
    await otherPage.goto(cliLink(OTHER_EMAIL));
    await setPassword(otherPage, PASSWORD, OTHER_NAME);
    secondCookie = await sessionCookie(other);
    await otherPage.goto("/profile/fill?step=documents");
    await expect(
      otherPage.getByText("No document yet. Add a file or paste a text above."),
    ).toBeVisible();
    expect(await listed(other)).toEqual([]);

    // By its id, the document does not exist for anyone else: the same answer as for an id
    // that never existed, whatever the route.
    const csrf = { "X-Requested-With": "jobapp" };
    const never = "00000000-0000-4000-8000-000000000000";
    for (const target of [id, never]) {
      const answers = [
        await other.request.get(`${UPLOAD}/${target}/text`),
        await other.request.post(`${UPLOAD}/${target}/confirm`, {
          headers: csrf,
        }),
        await other.request.delete(`${UPLOAD}/${target}`, { headers: csrf }),
      ];
      for (const answer of answers) {
        expect(answer.status()).toBe(404);
        expect(await answer.json()).toEqual({ detail: "Document not found." });
        expect(answer.headers()["cache-control"]).toBe("no-store");
      }
    }
    // The same file by the second user is a document of their own, not "already there".
    const same = await other.request.post(UPLOAD, {
      headers: { ...csrf, "X-Filename": "UTF-8''mine.md" },
      data: Buffer.from(DOSSIER, "utf8"),
    });
    expect(same.status()).toBe(201);
    expect(((await same.json()) as { duplicate: boolean }).duplicate).toBe(
      false,
    );
    expect((await listed(other)).map((item) => item.name)).toEqual(["mine.md"]);
  } finally {
    await otherPage.close();
  }
  // Nothing of the first user moved.
  expect(await listed(context)).toEqual(mine);
  const text = await context.request.get(`${UPLOAD}/${id}/text`);
  expect(((await text.json()) as { text: string }).text).toBe(DOSSIER);

  // Signed out, every route answers 401.
  const anonymous = await browser.newContext({ baseURL: caddyUrl });
  try {
    expect((await anonymous.request.get(UPLOAD)).status()).toBe(401);
    expect((await anonymous.request.get(`${UPLOAD}/${id}/text`)).status()).toBe(
      401,
    );
    expect(
      (
        await anonymous.request.post(UPLOAD, {
          headers: { "X-Requested-With": "jobapp" },
          data: Buffer.from("anonymous"),
        })
      ).status(),
    ).toBe(401);
  } finally {
    await anonymous.close();
  }
});

test("one account sends one file at a time, and an upload stopped on its way leaves nothing", async () => {
  test.setTimeout(180_000);
  const before = (await listed(context)).map((item) => item.name);
  // A slow link, so the file is still leaving for the rest of this test: 200 KiB a second,
  // through Caddy like everything else.
  const devtools = await context.newCDPSession(page);
  await devtools.send("Network.enable");
  const uploadSpeed = (bytesPerSecond: number) =>
    devtools.send("Network.emulateNetworkConditions", {
      offline: false,
      latency: 0,
      downloadThroughput: -1,
      uploadThroughput: bytesPerSecond,
    });
  await uploadSpeed(200 * 1024);
  try {
    await add(page, file(STOPPED, bigPdf(12 * MIB), "application/pdf"));
    const sending = queue(page)
      .getByRole("listitem")
      .filter({ hasText: STOPPED });
    // Bytes have left: the api has the request, and the account's turn is taken.
    await expect(sending.getByRole("progressbar")).toHaveAttribute(
      "aria-valuenow",
      /^[1-9]/,
    );

    // While it is on its way, the same account sends a second file from elsewhere. It waits
    // for its turn without being read, then is refused: one account never takes both places.
    // The refusal comes through Caddy with its sentence, although its body was still leaving.
    const started = Date.now();
    const second = curlAsync(UPLOAD, cookie, bigPdf(12 * MIB));
    // Another user is held up by neither: their file goes through at once.
    const theirs = await secondUser?.request.post(UPLOAD, {
      headers: {
        "X-Requested-With": "jobapp",
        "X-Filename": "UTF-8''meanwhile.md",
      },
      data: Buffer.from(
        "# Meanwhile\n\nSent while another account was busy.\n",
      ),
    });
    expect(theirs?.status()).toBe(201);
    expect(Date.now() - started).toBeLessThan(10_000);
    const refused = await second;
    expect(refused.status).toBe(429);
    expect(refused.type).toContain("application/json");
    expect(JSON.parse(refused.body)).toEqual({
      detail:
        "Another upload of yours is still on its way. Wait for it to finish, then add this one.",
    });
    // It waited 35 s, longer than the 30 s after which an upload that died gives its turn
    // back: a file sent after such an upload is taken, and only one behind a live upload is
    // refused.
    expect(Date.now() - started).toBeGreaterThan(30_000);

    // The first file is still on its way: the user stops it.
    await sending.getByRole("button", { name: `Cancel ${STOPPED}` }).click();
  } finally {
    await uploadSpeed(-1);
    await devtools.detach();
  }
  await expect(queue(page)).toHaveCount(0);
  await expect(
    page.getByRole("status").filter({ hasText: `Stopped sending ${STOPPED}.` }),
  ).toHaveCount(1);
  await expect(
    page.getByRole("button", { name: "Choose files" }),
  ).toBeFocused();

  // The api let go of it: the same user's next file goes through at once. Were the stopped
  // body still held, this one would wait for its turn and then be refused.
  await add(
    page,
    file("after the stop.md", "# Notes\n\nAdded after a stop.\n"),
  );
  await expect(row(page, "after the stop.md")).toContainText("Ready", {
    timeout: 10_000,
  });
  const names = (await listed(context)).map((item) => item.name);
  expect([...names].sort()).toEqual([...before, "after the stop.md"].sort());
});

test("the largest file allowed goes through the page and through Caddy", async () => {
  test.setTimeout(180_000);
  await add(page, file("portfolio.pdf", bigPdf(FILE_MAX), "application/pdf"));
  const portfolio = row(page, "portfolio.pdf");
  // Stored whole, then given to the reader, which finds 20 MiB of noise: the row says so.
  await expect(portfolio).toContainText(/Being read|Could not be read/, {
    timeout: 120_000,
  });
  await expect(portfolio).toContainText("Could not be read", {
    timeout: 120_000,
  });
  await expect(portfolio).toContainText(
    "This PDF could not be read. It may be damaged",
  );
  await expect(portfolio).toContainText("PDF, 20 MB");
  const stored = (await listed(context)).find(
    (item) => item.name === "portfolio.pdf",
  );
  expect(stored?.size).toBe(FILE_MAX);
  // Deleting makes room again.
  const removed = await context.request.delete(
    `${UPLOAD}/${stored?.id ?? ""}`,
    {
      headers: { "X-Requested-With": "jobapp" },
    },
  );
  expect(removed.status()).toBe(204);
  await page.reload();
  await expect(row(page, "portfolio.pdf")).toHaveCount(0);
});

test("20 MiB is accepted on the upload route only: every other route keeps 10 MiB", async () => {
  test.setTimeout(300_000);
  const ids: string[] = [];
  const kept = (answer: Answer) => {
    expect(answer.status).toBe(201);
    ids.push((JSON.parse(answer.body) as { id: string }).id);
  };

  // The upload route: 20 MiB exactly passes, one byte more is refused before the api sees it.
  kept(curl(UPLOAD, cookie, bigPdf(FILE_MAX)));
  expect(fromCaddy(curl(UPLOAD, cookie, bigPdf(FILE_MAX + 1)))).toBe(true);
  // Without a Content-Length: between the two limits it passes, over 20 MiB it is cut.
  kept(curl(UPLOAD, cookie, bigPdf(12 * MIB), CHUNKED));
  expect(curl(UPLOAD, cookie, bigPdf(FILE_MAX + 1), CHUNKED).status).toBe(413);

  // Every other spelling and every other route: 10 MiB, as before.
  const over = Buffer.alloc(10 * MIB + 1, 0x61);
  const others: Array<[string, string[]]> = [
    [`${UPLOAD}/text`, []],
    [`${UPLOAD}/`, []],
    [UPLOAD, ["-X", "PUT"]],
    [UPLOAD, ["-X", "PATCH"]],
    [UPLOAD.toUpperCase(), []],
    // Spellings a proxy could clean into the route, and the api answers 404.
    ["/api/ingestion//documents", []],
    ["/api/ingestion/./documents", []],
    ["/api//ingestion/documents", []],
    [`${UPLOAD}/00000000-0000-4000-8000-000000000000/confirm`, []],
    ["/api/ingestion/other", []],
    ["/api/profile/photo", ["-X", "PUT"]],
    ["/api/profile/import", []],
    ["/api/health", []],
    ["/", []],
  ];
  for (const [target, extra] of others) {
    const answer = curl(target, cookie, over, extra);
    expect(fromCaddy(answer), `${extra.join(" ")} ${target}`).toBe(true);
  }
  for (const target of [`${UPLOAD}/text`, "/api/profile/import"]) {
    const answer = curl(target, cookie, bigPdf(12 * MIB), CHUNKED);
    expect(answer.status, `chunked ${target}`).toBe(413);
  }
  // 10 MiB exactly still reaches the api on another route: the refusal is the api's own
  // (JSON, the paste limit), not Caddy's.
  const atLimit = curl(`${UPLOAD}/text`, cookie, Buffer.alloc(10 * MIB, 0x61));
  expect(atLimit.status).toBe(413);
  expect(atLimit.type).toContain("application/json");
  expect(atLimit.body).toContain("longer than 100,000 characters");

  for (const id of ids) {
    const removed = await context.request.delete(`${UPLOAD}/${id}`, {
      headers: { "X-Requested-With": "jobapp" },
    });
    expect(removed.status()).toBe(204);
  }
});

test("the sign in routes take 64 KiB through Caddy, and a body with no session is the api's 401", () => {
  // Issue #100, through the real proxy and with no cookie at all. The routes under /api/auth/
  // are the only ones that read a body from a caller with no session, and Caddy gives them
  // their own small class.
  const nobody = "";
  const publicMax = 64 * 1024;
  const login = "/api/auth/login";
  // One byte over, declared: Caddy's own refusal, before the api sees anything.
  expect(
    fromCaddy(curl(login, nobody, Buffer.alloc(publicMax + 1, 0x61))),
  ).toBe(true);
  // Without a Content-Length it is cut at the same limit.
  expect(
    curl(login, nobody, Buffer.alloc(publicMax + 1, 0x61), CHUNKED).status,
  ).toBe(413);
  // 64 KiB exactly reaches the api: the answer is the api's JSON (the body is no sign in).
  const atLimit = curl(login, nobody, Buffer.alloc(publicMax, 0x61));
  expect(atLimit.status).not.toBe(413);
  expect(atLimit.type).toContain("application/json");
  // A route that is not public: 200 KB passes Caddy (10 MiB there), and the api answers 401
  // without reading it: no session, no body.
  const gated = curl("/api/jobs/import", nobody, Buffer.alloc(200_000, 0x61));
  expect(gated.status).toBe(401);
  expect(gated.type).toContain("application/json");
  expect(gated.body).toContain("Sign in to continue");
});

test("two uploads of 20 MiB at once, by two users, stay far inside the api's memory limit", async () => {
  test.skip(!project, "needs the Compose project (E2E_COMPOSE_PROJECT)");
  test.setTimeout(300_000);
  const read = (service: string, name: string) =>
    compose(
      "exec",
      "-T",
      service,
      "sh",
      "-c",
      `cat /sys/fs/cgroup/${name} 2>/dev/null || true`,
    ).trim();
  const limit = Number(read("api", "memory.max"));
  const before = Number(read("api", "memory.current"));

  // The api's own cgroup, read every 100 ms for 12 s from inside the container.
  const samples: number[] = [];
  const sampler = spawn(
    "docker",
    [
      "compose",
      "-p",
      project ?? "",
      "-f",
      "compose.yml",
      "exec",
      "-T",
      "api",
      "sh",
      "-c",
      "i=0; while [ $i -lt 120 ]; do cat /sys/fs/cgroup/memory.current; sleep 0.1; i=$((i+1)); done",
    ],
    { cwd: root },
  );
  sampler.stdout.on("data", (data: Buffer) => {
    for (const line of data.toString("utf8").split("\n"))
      if (/^\d+$/.test(line.trim())) samples.push(Number(line.trim()));
  });
  const sampled = new Promise<void>((resolve) => {
    sampler.on("close", () => {
      resolve();
    });
  });
  await expect
    .poll(() => samples.length, { timeout: 30_000 })
    .toBeGreaterThan(5);

  // Two users: one account sends one file at a time, so two of its own would never overlap.
  expect(secondCookie).not.toBe("");
  const started = Date.now();
  const answers = await Promise.all([
    curlAsync(UPLOAD, cookie, bigPdf(FILE_MAX)),
    curlAsync(UPLOAD, secondCookie, bigPdf(FILE_MAX)),
  ]);
  const seconds = (Date.now() - started) / 1000;
  await sampled;
  expect(answers.map((answer) => answer.status)).toEqual([201, 201]);

  const highest = Math.max(...samples);
  const peak = Number(read("api", "memory.peak")) || highest;
  const events = read("api", "memory.events");
  const measured = {
    date: new Date().toISOString(),
    what: "api container, two uploads of 20 MiB at once by two users, through Caddy",
    limit_bytes: limit,
    before_bytes: before,
    highest_sample_bytes: highest,
    rise_bytes: highest - before,
    peak_since_start_bytes: peak,
    samples: samples.length,
    seconds_for_both: seconds,
    oom_kill: /oom_kill (\d+)/.exec(events)?.[1] ?? "unknown",
    db_peak_bytes: Number(read("db", "memory.peak")) || null,
    db_limit_bytes: Number(read("db", "memory.max")) || null,
  };
  const folder = path.join(
    root,
    "tasks/evidence",
    process.env.E2E_EVIDENCE ?? "ingestion-g1",
  );
  fs.mkdirSync(folder, { recursive: true });
  fs.writeFileSync(
    path.join(folder, "memory.json"),
    `${JSON.stringify(measured, null, 2)}\n`,
  );
  console.log(`   api memory: ${JSON.stringify(measured)}`);

  expect(limit).toBe(512 * MIB);
  expect(measured.oom_kill).toBe("0");
  // Far inside the limit, since the whole journey (peak), and both bodies held once each.
  expect(peak).toBeLessThan(0.75 * limit);
  expect(highest - before).toBeLessThan(4 * FILE_MAX);

  const owners = [context, secondUser];
  for (const [index, answer] of answers.entries()) {
    const id = (JSON.parse(answer.body) as { id: string }).id;
    const removed = await owners[index]?.request.delete(`${UPLOAD}/${id}`, {
      headers: { "X-Requested-With": "jobapp" },
    });
    expect(removed?.status()).toBe(204);
  }
});

test("no log holds a name or a word of a document", async () => {
  test.skip(!project, "needs the Compose project (E2E_COMPOSE_PROJECT)");
  const stored = await context.request.post(UPLOAD, {
    headers: {
      "X-Requested-With": "jobapp",
      "X-Filename": `UTF-8''${MARKER_NAME}.txt`,
    },
    data: Buffer.from(`${MARKER_TEXT} key=${KEY_LIKE}`, "utf8"),
  });
  expect(stored.status()).toBe(201);
  const pasted = await context.request.post(`${UPLOAD}/text`, {
    headers: { "X-Requested-With": "jobapp" },
    data: { text: `pasted ${MARKER_TEXT}`, name: MARKER_NAME },
  });
  expect(pasted.status()).toBe(201);
  const id = ((await stored.json()) as { id: string }).id;
  await context.request.get(`${UPLOAD}/${id}/text`);
  await context.request.delete(`${UPLOAD}/${id}`, {
    headers: { "X-Requested-With": "jobapp" },
  });

  // The upload stopped on its way was never kept, long after the page looked again.
  expect((await listed(context)).map((item) => item.name)).not.toContain(
    STOPPED,
  );

  const logs = compose("logs", "--no-color", "--no-log-prefix", "api", "caddy");
  // The requests of this journey are there (ids, sizes, rule names)...
  expect(logs).toContain("document stored");
  expect(logs).toContain('"rule": "gif"');
  // ...the stopped upload among them: the api was still reading it when the browser left.
  expect(logs).toContain('"rule": "upload-interrupted"');
  expect(logs).toContain("aws-access-key");
  expect(logs).toContain("/api/ingestion/documents");
  // ...and nothing of what the documents are called or say.
  for (const hidden of [
    MARKER_NAME,
    MARKER_TEXT,
    KEY_LIKE,
    "dossier.md",
    "dossier%20copy",
    "stopped%20on",
    "cover%20letter",
    "Robin%20Example",
    "Acme Robotics",
    "X-Filename",
    "x-filename",
  ])
    expect(logs, hidden).not.toContain(hidden);
});

test("the journey left no error in the browser console", () => {
  expect(errors).toEqual([]);
});
