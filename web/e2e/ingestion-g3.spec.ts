import { expect, test, type BrowserContext, type Page } from "@playwright/test";

import {
  caddyUrl,
  clearAttempts,
  cliLink,
  makeShots,
  newContext,
  setPassword,
  watchErrors,
} from "./stack";

const takeShots = makeShots("ingestion-g3", "ingestion-g3");

/**
 * Screenshots once every toast is gone, so none covers the page. A full page is taken from its
 * top with the header in the flow: a sticky header is painted where the page was scrolled to,
 * across whatever is there.
 */
async function shots(page: Page, name: string) {
  await expect(page.locator("[data-sonner-toast]")).toHaveCount(0, {
    timeout: 15_000,
  });
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
 * Ingestion G3, the laptop-analysis prompt, against the built stack through Caddy: a new user
 * opens step 1 of /profile/fill, reads the prompt for Claude Code and for Codex, copies it in
 * one click (the clipboard holds exactly what the api sent, with the user's name), then pastes
 * a dossier written the way the prompt asks in step 2. Every name and every line of the dossier
 * is made up, and the document is deleted at the end.
 *
 * Locally, on a scratch Compose project (its own volumes, torn down afterwards):
 *   HTTP_PORT=8093 docker compose -p jobapp-g3 -f compose.yml -f compose.mac.yml up -d --build --wait
 *   docker compose -p jobapp-g3 -f compose.yml exec -T api alembic upgrade head
 *   E2E_CADDY_URL=http://127.0.0.1:8093 E2E_COMPOSE_PROJECT=jobapp-g3 \
 *     pnpm --dir web exec playwright test -c e2e/ingestion-all.config.ts ingestion-g3
 *   docker compose -p jobapp-g3 -f compose.yml down -v
 */
test.describe.configure({ mode: "serial" });
test.skip(!caddyUrl, "set E2E_CADDY_URL with the Compose stack running");

const run = Date.now().toString(36);
const EMAIL = `e2e-ingestion-g3-${run}@example.org`;
// Quotes, a brace and a backtick: the name reaches the prompt as typed, on one line.
const NAME = 'Camille "Cam" {Example} `e2e`';
const PASSWORD = `prompt journey ${run} passphrase`;
const PROMPT_URL = "/api/ingestion/prompt";
const DOCUMENTS_URL = "/api/ingestion/documents";

interface Prompt {
  version: number;
  sha256: string;
  output_folder: string;
  claude_code: string;
  codex: string;
}

interface Listed {
  id: string;
  name: string;
  status: string;
}

/** What goes under each heading of the dossier: invented, with dates as the prompt asks. */
const SECTIONS: Record<string, string> = {
  Identity:
    "Camille Example, data engineer, Lyon, France. French (native), English (fluent). (evidence: cv/camille-example.md)",
  Experience:
    "### Data engineer, Example Rail (2021-03 to present)\n\nLyon, permanent contract. (evidence: cv/camille-example.md)\n\n### Master in computer science, Example University (2018 to 2020)\n\nLyon. (evidence: cv/camille-example.md)",
  Facts:
    "A timetable import service that reads the operators' files each night and publishes one clean feed. I wrote the validation step. [unsure] The year it went live is not in the files. (evidence: projects/timetable/README.md)",
  Skills:
    "Python, used for real work on the timetable import. (evidence: projects/timetable/pyproject.toml)",
  "Search preferences": "Nothing found.",
  "Application answers": "Nothing found.",
  "Open questions": "When did the timetable import go live?",
};

/** The dossier an assistant would write: the block the prompt gives, each heading filled. */
function dossierFrom(prompt: string): string {
  const skeleton = prompt.split("```\n")[1] ?? "";
  return skeleton
    .split("\n")
    .map((line) => {
      const section = SECTIONS[line.replace(/^## /, "")];
      return line.startsWith("## ") && section ? `${line}\n\n${section}` : line;
    })
    .join("\n");
}

async function listed(context: BrowserContext): Promise<Listed[]> {
  const response = await context.request.get(DOCUMENTS_URL);
  expect(response.status()).toBe(200);
  return ((await response.json()) as { items: Listed[] }).items;
}

const steps = (page: Page) => page.getByRole("navigation", { name: "Steps" });
const promptBox = (page: Page, assistant: string) =>
  page.getByRole("textbox", { name: `Prompt for ${assistant}` });
const copyStatus = (page: Page) =>
  page.getByRole("tabpanel").getByRole("status");

let context: BrowserContext;
let page: Page;
let errors: string[];
let prompt: Prompt;

test.beforeAll(async ({ browser }) => {
  context = await newContext(browser);
  page = await context.newPage();
  errors = watchErrors(page);
});

test.afterAll(async () => {
  await context.close();
  clearAttempts([EMAIL]);
});

test("a new user opens Fill my profile on the prompt, filled with their name", async () => {
  await page.goto(cliLink(EMAIL));
  await setPassword(page, PASSWORD, NAME);
  await page.goto("/profile");
  await page
    .getByRole("link", { name: "Fill my profile with an agent" })
    .click();
  await expect(page).toHaveURL(/\/profile\/fill$/);
  await expect(
    page.getByRole("heading", {
      level: 2,
      name: "Step 1: Prepare a dossier with a coding assistant",
    }),
  ).toBeVisible();
  await expect(
    steps(page).getByRole("button", { name: /Step 1: Prepare/ }),
  ).toHaveAttribute("aria-current", "step");

  // What the api answers for this user: the page shows exactly that.
  const response = await context.request.get(PROMPT_URL);
  expect(response.status()).toBe(200);
  expect(response.headers()["cache-control"]).toBe("no-store");
  prompt = (await response.json()) as Prompt;
  expect(prompt.version).toBeGreaterThanOrEqual(1);
  expect(prompt.sha256).toMatch(/^[0-9a-f]{64}$/);
  expect(prompt.claude_code).toContain(`My name: ${NAME}\n`);
  expect(prompt.codex).toContain(`My name: ${NAME}\n`);
  // Nothing else of the account is in it.
  expect(JSON.stringify(prompt)).not.toContain(EMAIL);
  expect(JSON.stringify(prompt)).not.toContain("example.org");

  await expect(
    page.getByRole("tab", { name: "Claude Code", selected: true }),
  ).toBeVisible();
  await expect(promptBox(page, "Claude Code")).toHaveValue(prompt.claude_code);
  await expect(promptBox(page, "Claude Code")).toHaveAttribute("readonly");
  await expect(
    page.getByText(
      "Claude Code or Codex sends what it reads to its own model provider, under your own account. This app never reads your computer: it only gets the file you add in the next step.",
    ),
  ).toBeVisible();
  await expect(
    page
      .getByRole("tabpanel")
      .getByRole("listitem")
      .filter({ hasText: "start Claude Code by typing claude" }),
  ).toBeVisible();
  await shots(page, "01-prompt-claude-code");
});

test("the Codex tab holds the prompt for Codex, and the keyboard goes back", async () => {
  await page.getByRole("tab", { name: "Codex" }).click();
  await expect(
    page.getByRole("tab", { name: "Codex", selected: true }),
  ).toBeVisible();
  await expect(promptBox(page, "Codex")).toHaveValue(prompt.codex);
  await expect(promptBox(page, "Claude Code")).toHaveCount(0);
  await expect(
    page
      .getByRole("tabpanel")
      .getByRole("listitem")
      .filter({ hasText: "start Codex by typing codex" }),
  ).toBeVisible();
  expect(prompt.codex).not.toBe(prompt.claude_code);
  await shots(page, "02-prompt-codex");

  await page.getByRole("tab", { name: "Codex" }).focus();
  await page.keyboard.press("ArrowLeft");
  await expect(
    page.getByRole("tab", { name: "Claude Code", selected: true }),
  ).toBeFocused();
  await expect(promptBox(page, "Claude Code")).toHaveValue(prompt.claude_code);
});

test("one click copies exactly what the api sent, for each assistant", async () => {
  const panel = page.getByRole("tabpanel");
  await expect(copyStatus(page)).toHaveText("");
  await panel.getByRole("button", { name: "Copy the prompt" }).click();
  await expect(panel.getByRole("button", { name: "Copied" })).toBeVisible();
  await expect(copyStatus(page)).toHaveText("The prompt is on your clipboard.");
  await expect(page.getByText("Prompt copied.", { exact: true })).toBeVisible();
  const copied = await page.evaluate(() => navigator.clipboard.readText());
  expect(copied).toBe(prompt.claude_code);
  expect(copied).toContain(`My name: ${NAME}\n`);
  await shots(page, "03-copied");

  await page.getByRole("tab", { name: "Codex" }).click();
  await expect(copyStatus(page)).toHaveText("");
  await panel.getByRole("button", { name: "Copy the prompt" }).click();
  await expect(panel.getByRole("button", { name: "Copied" })).toBeVisible();
  expect(await page.evaluate(() => navigator.clipboard.readText())).toBe(
    prompt.codex,
  );
  await page.getByRole("tab", { name: "Claude Code" }).click();
});

test("when the browser refuses the clipboard, the prompt is selected to copy by hand", async () => {
  await page.evaluate(() => {
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: {
        writeText: () => Promise.reject(new Error("refused by the journey")),
      },
    });
  });
  const panel = page.getByRole("tabpanel");
  await panel.getByRole("button", { name: "Copy the prompt" }).click();
  await expect(copyStatus(page)).toHaveText(
    "This browser blocked copying. The prompt is selected: press Ctrl+C, or Cmd+C on a Mac.",
  );
  const box = promptBox(page, "Claude Code");
  await expect(box).toBeFocused();
  const selected = await box.evaluate((element: HTMLTextAreaElement) =>
    element.value.slice(element.selectionStart, element.selectionEnd),
  );
  expect(selected).toBe(prompt.claude_code);
  await expect(panel.getByRole("button", { name: "Copied" })).toHaveCount(0);
  await shots(page, "04-copy-by-hand");
  // A reload gives the browser's clipboard back for the rest of the journey.
  await page.reload();
  await expect(promptBox(page, "Claude Code")).toHaveValue(prompt.claude_code);
});

test("signed out, the prompt is not served", async ({ browser }) => {
  const stranger = await browser.newContext({ baseURL: caddyUrl });
  try {
    const response = await stranger.request.get(PROMPT_URL);
    expect(response.status()).toBe(401);
    expect(response.headers()["cache-control"]).toBe("no-store");
    expect(await response.text()).not.toContain("dossier");
  } finally {
    await stranger.close();
  }
});

test("a dossier written the way the prompt asks is pasted in step 2, then deleted", async () => {
  const dossier = dossierFrom(prompt.claude_code);
  expect(dossier.startsWith(`dossier-format: ${prompt.version}\n`)).toBe(true);
  for (const [heading, written] of Object.entries(SECTIONS))
    expect(dossier).toContain(`## ${heading}\n\n${written}`);

  await page.getByRole("button", { name: "Go on to your documents" }).click();
  await expect(page).toHaveURL(/\/profile\/fill\?step=documents$/);
  await expect(
    page.getByRole("heading", { level: 2, name: "Step 2: Add your documents" }),
  ).toBeVisible();
  expect(await listed(context)).toEqual([]);

  await page.getByLabel("Or paste a text").fill(dossier);
  await page.getByRole("button", { name: "Add this text" }).click();
  await expect(page.getByText("Text added.", { exact: true })).toBeVisible();
  const row = page
    .getByRole("list", { name: "Your documents" })
    .getByRole("listitem")
    .filter({ hasText: "Pasted text" });
  await expect(row).toContainText("Ready");
  // No secret warning: the dossier holds none.
  await expect(row.getByText(/Possible secret/)).toHaveCount(0);

  const stored = await listed(context);
  expect(stored).toHaveLength(1);
  const id = stored[0]?.id ?? "";
  const text = await context.request.get(`${DOCUMENTS_URL}/${id}/text`);
  expect(text.status()).toBe(200);
  // Word for word what was pasted (the ends of the text aside).
  expect(((await text.json()) as { text: string }).text.trim()).toBe(
    dossier.trim(),
  );
  await shots(page, "05-dossier-pasted");

  await row.getByRole("button", { name: "Delete Pasted text" }).click();
  const dialog = page.getByRole("alertdialog", {
    name: "Delete this document?",
  });
  await dialog.getByRole("button", { name: "Delete for good" }).click();
  await expect(page.getByText("Document deleted.")).toBeVisible();
  await expect(row).toHaveCount(0);
  expect(await listed(context)).toEqual([]);

  // Back to step 1 from the steps: the prompt is still there.
  await steps(page)
    .getByRole("button", { name: /Step 1: Prepare/ })
    .click();
  await expect(promptBox(page, "Claude Code")).toHaveValue(prompt.claude_code);
});

test("the journey left no error in the browser console", () => {
  expect(errors).toEqual([]);
});
