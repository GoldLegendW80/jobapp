import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it, vi } from "vitest";

import { fakeSession, member } from "@/test/auth";
import { fakeDocuments, fakePrompt, PROMPT } from "@/test/ingestion";
import { renderApp } from "@/test/render";
import { server } from "@/test/server";

import { promptSchema } from "./prompt-api";

type WriteText = (text: string) => Promise<void>;

/** After userEvent.setup(), which installs a clipboard of its own. */
function setClipboard(writeText: WriteText | null) {
  Object.defineProperty(navigator, "clipboard", {
    configurable: true,
    value: writeText ? { writeText } : undefined,
  });
}

async function open() {
  fakeSession(member);
  fakeDocuments();
  const calls = fakePrompt();
  const app = renderApp("/profile/fill");
  await screen.findByRole("tablist", { name: "Your coding assistant" });
  return { app, calls };
}

const panel = () => screen.getByRole("tabpanel");
const promptBox = (assistant: string) =>
  screen.getByRole<HTMLTextAreaElement>("textbox", {
    name: `Prompt for ${assistant}`,
  });
const copyButton = () =>
  within(panel()).getByRole("button", { name: "Copy the prompt" });
const status = () => within(panel()).getByRole("status");

describe("step 1 of /profile/fill, the prompt", () => {
  it("shows the prompt for Claude Code first, whole and read only", async () => {
    await open();
    expect(
      screen.getByRole("tab", { name: "Claude Code", selected: true }),
    ).toBeVisible();
    expect(
      screen.getByRole("tab", { name: "Codex", selected: false }),
    ).toBeVisible();
    const box = promptBox("Claude Code");
    expect(box).toHaveValue(PROMPT.claude_code);
    expect(box).toHaveAttribute("readonly");
    expect(box.value).toContain(`My name: «${member.name}»`);
    // The other prompt is not on the page until its tab is opened.
    expect(
      screen.queryByRole("textbox", { name: "Prompt for Codex" }),
    ).toBeNull();
    expect(screen.getAllByRole("textbox")).toHaveLength(1);
  });

  it("says in four short steps what to do with it", async () => {
    await open();
    const steps = within(panel()).getAllByRole("listitem");
    expect(steps.map((step) => step.textContent)).toEqual([
      "Copy the prompt below.",
      "Open a terminal on your computer and start Claude Code by typing claude.",
      "Paste the prompt, replace the two folder lines near the top with the folders that hold your work, then send it.",
      "Read the file it writes, ~/jobapp-dossier/dossier.md, then add it in step 2.",
    ]);
  });

  it("states plainly where the files go, and that the step is optional", async () => {
    await open();
    expect(
      screen.getByText(
        "Claude Code or Codex sends what it reads to its own model provider, under your own account. This app never reads your computer: it only gets the file you add in the next step.",
      ),
    ).toBeVisible();
    expect(screen.getByText("Where your files go")).toBeVisible();
    expect(screen.getByText(/This step is optional\./)).toBeVisible();
  });

  it("names the version of the prompt and asks the user to read it", async () => {
    await open();
    const box = promptBox("Claude Code");
    expect(box).toHaveAccessibleDescription(
      "Prompt version 1. Read it before you run it: it lists what the assistant may read and what it must leave alone.",
    );
  });

  it("switches to the prompt for Codex, with its own instructions", async () => {
    const user = userEvent.setup();
    const { calls } = await open();
    await user.click(screen.getByRole("tab", { name: "Codex" }));
    expect(
      screen.getByRole("tab", { name: "Codex", selected: true }),
    ).toBeVisible();
    expect(promptBox("Codex")).toHaveValue(PROMPT.codex);
    expect(screen.getAllByRole("textbox")).toHaveLength(1);
    expect(
      within(panel()).getByText(/start Codex by typing/),
    ).toHaveTextContent(
      "Open a terminal on your computer and start Codex by typing codex.",
    );
    await user.click(screen.getByRole("tab", { name: "Claude Code" }));
    expect(promptBox("Claude Code")).toHaveValue(PROMPT.claude_code);
    // Both texts came with the one answer, asked at the route's own address.
    expect(calls.count).toBe(1);
    expect(calls.paths).toEqual(["/api/ingestion/prompt"]);
  });

  it("copies exactly the text the api sent, and says so", async () => {
    const user = userEvent.setup();
    const writeText = vi.fn<WriteText>(() => Promise.resolve());
    setClipboard(writeText);
    await open();
    expect(status()).toBeEmptyDOMElement();
    await user.click(copyButton());
    expect(writeText).toHaveBeenCalledExactlyOnceWith(PROMPT.claude_code);
    expect(
      await within(panel()).findByRole("button", { name: "Copied" }),
    ).toBeVisible();
    expect(status()).toHaveTextContent("The prompt is on your clipboard.");
    expect(await screen.findByText("Prompt copied.")).toBeInTheDocument();
  });

  it("copies the prompt of the open tab, and Copied belongs to that tab only", async () => {
    const user = userEvent.setup();
    const writeText = vi.fn<WriteText>(() => Promise.resolve());
    setClipboard(writeText);
    await open();
    await user.click(screen.getByRole("tab", { name: "Codex" }));
    await user.click(copyButton());
    expect(writeText).toHaveBeenCalledExactlyOnceWith(PROMPT.codex);
    await within(panel()).findByRole("button", { name: "Copied" });
    await user.click(screen.getByRole("tab", { name: "Claude Code" }));
    expect(copyButton()).toBeVisible();
    expect(status()).toBeEmptyDOMElement();
  });

  it.each([
    ["refuses", () => vi.fn<WriteText>(() => Promise.reject(new Error("no")))],
    ["is missing", () => null],
  ])(
    "when the clipboard %s, it selects the whole prompt and says how to copy by hand",
    async (_, make) => {
      const user = userEvent.setup();
      setClipboard(make());
      await open();
      await user.click(copyButton());
      await waitFor(() => {
        expect(status()).toHaveTextContent(
          "This browser blocked copying. The prompt is selected: copy it as you usually do, or select the text and copy it.",
        );
      });
      const box = promptBox("Claude Code");
      expect(box).toHaveFocus();
      expect(box.selectionStart).toBe(0);
      expect(box.selectionEnd).toBe(PROMPT.claude_code.length);
      // Nothing claims a copy that did not happen.
      expect(
        within(panel()).queryByRole("button", { name: "Copied" }),
      ).toBeNull();
    },
  );

  it("copies again after a refusal once the browser allows it", async () => {
    const user = userEvent.setup();
    const writeText = vi
      .fn<WriteText>()
      .mockRejectedValueOnce(new Error("no"))
      .mockResolvedValue(undefined);
    setClipboard(writeText);
    await open();
    await user.click(copyButton());
    await waitFor(() => {
      expect(status()).toHaveTextContent(/blocked copying/);
    });
    await user.click(copyButton());
    await within(panel()).findByRole("button", { name: "Copied" });
    expect(status()).toHaveTextContent("The prompt is on your clipboard.");
  });

  it("works from the keyboard: arrows change the tab, Enter copies", async () => {
    const user = userEvent.setup();
    const writeText = vi.fn<WriteText>(() => Promise.resolve());
    setClipboard(writeText);
    await open();
    screen.getByRole("tab", { name: "Claude Code" }).focus();
    await user.keyboard("{ArrowRight}");
    expect(screen.getByRole("tab", { name: "Codex" })).toHaveFocus();
    expect(
      screen.getByRole("tab", { name: "Codex", selected: true }),
    ).toBeVisible();
    // From the tab: the panel, then the first control in it, the Copy button.
    await user.tab();
    await user.tab();
    expect(copyButton()).toHaveFocus();
    await user.keyboard("{Enter}");
    expect(writeText).toHaveBeenCalledExactlyOnceWith(PROMPT.codex);
    // Then the prompt itself, which a keyboard user can read and select.
    await user.tab();
    expect(promptBox("Codex")).toHaveFocus();
  });

  it("names every control for a screen reader", async () => {
    await open();
    expect(
      screen.getByRole("tablist", { name: "Your coding assistant" }),
    ).toBeVisible();
    expect(screen.getAllByRole("tab")).toHaveLength(2);
    expect(panel()).toHaveAccessibleName("Claude Code");
    expect(copyButton()).toHaveAccessibleDescription("");
    expect(promptBox("Claude Code")).toBeVisible();
    // The icons say nothing.
    for (const icon of panel().querySelectorAll("svg"))
      expect(icon).toHaveAttribute("aria-hidden", "true");
  });

  it("says when the prompt could not be loaded, and Retry asks again", async () => {
    const user = userEvent.setup();
    fakeSession(member);
    fakeDocuments();
    let down = true;
    server.use(
      http.get("*/api/ingestion/prompt", () =>
        down
          ? HttpResponse.json({ detail: "secret words" }, { status: 500 })
          : HttpResponse.json(PROMPT),
      ),
    );
    renderApp("/profile/fill");
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("The prompt could not be loaded");
    expect(alert).toHaveTextContent(
      "The server could not answer. Try again in a moment.",
    );
    expect(alert).not.toHaveTextContent("secret words");
    expect(screen.queryByRole("tablist")).toBeNull();
    // The way on stays open: the step is optional.
    expect(
      screen.getByRole("button", { name: "Go on to your documents" }),
    ).toBeVisible();
    down = false;
    await user.click(screen.getByRole("button", { name: "Retry" }));
    await screen.findByRole("tablist", { name: "Your coding assistant" });
    expect(promptBox("Claude Code")).toHaveValue(PROMPT.claude_code);
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("does not show an answer it cannot read", async () => {
    fakeSession(member);
    fakeDocuments();
    server.use(
      http.get("*/api/ingestion/prompt", () =>
        HttpResponse.json({ ...PROMPT, codex: "" }),
      ),
    );
    renderApp("/profile/fill");
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "The server sent an answer this page cannot read. Reload the page.",
    );
    expect(screen.queryByRole("textbox")).toBeNull();
  });

  it("goes on to the documents", async () => {
    const user = userEvent.setup();
    const { app } = await open();
    await user.click(
      screen.getByRole("button", { name: "Go on to your documents" }),
    );
    await screen.findByRole("heading", {
      level: 2,
      name: "Step 2: Add your documents",
    });
    expect(app.location()).toBe("/profile/fill?step=documents");
  });
});

describe("the answer of GET /api/ingestion/prompt", () => {
  it("is read when it holds both texts, the version and the hash", () => {
    expect(promptSchema.parse(PROMPT)).toEqual(PROMPT);
  });

  it.each([
    ["no version", { ...PROMPT, version: undefined }],
    ["no hash", { ...PROMPT, sha256: undefined }],
    ["an empty prompt", { ...PROMPT, claude_code: "" }],
    ["no folder", { ...PROMPT, output_folder: "" }],
    ["a list", [PROMPT]],
  ])("is refused with %s", (_, body) => {
    expect(promptSchema.safeParse(body).success).toBe(false);
  });
});
