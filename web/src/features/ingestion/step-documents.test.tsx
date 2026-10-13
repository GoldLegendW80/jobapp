import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { toast } from "sonner";
import { afterEach, describe, expect, it } from "vitest";

import { fakeSession, member } from "@/test/auth";
import { fakeDocument, fakeDocuments, LIMITS } from "@/test/ingestion";
import { renderApp } from "@/test/render";
import { server } from "@/test/server";

import { DELETE_PROMISE } from "./delete-document";
import type { IngestionDocument } from "./documents-api";
import { WATCH_MS } from "./labels";

afterEach(() => {
  toast.dismiss();
});

async function open(initial: IngestionDocument[] = []) {
  fakeSession(member);
  const state = fakeDocuments(initial);
  const user = userEvent.setup({ applyAccept: false });
  renderApp("/profile/fill?step=documents");
  await screen.findByRole("heading", { level: 3, name: "Your documents" });
  await waitFor(() => {
    expect(screen.queryByText("Loading your documents")).toBeNull();
  });
  return { user, state, input: screen.getByTestId("document-files") };
}

const text = (content: string, name: string) =>
  new File([content], name, { type: "text/plain" });
const documents = () => screen.getByRole("list", { name: "Your documents" });
const rows = () => within(documents()).getAllByRole("listitem");
const row = (name: string) => {
  const found = rows().find((item) => item.textContent.includes(name));
  if (!found) throw new Error(`no row for ${name}`);
  return within(found);
};
/** Wait until a document of that name is in the list (not merely on its way). */
const listed = async (name: string) => {
  const list = await screen.findByRole("list", { name: "Your documents" });
  expect(await within(list).findByText(name)).toBeVisible();
};
const queue = () => screen.getByRole("list", { name: "Files being added" });
/** The polite live region of the file uploads. */
const announced = () =>
  screen
    .getAllByRole("status")
    .map((region) => region.textContent)
    .join(" | ");

describe("documents: the list", () => {
  it("says so when there is no document yet", async () => {
    await open();
    expect(
      screen.getByText("No document yet. Add a file or paste a text above."),
    ).toBeVisible();
    expect(screen.queryByText(/used$/)).toBeNull();
  });

  it("says exactly who can see the documents", async () => {
    await open();
    expect(
      screen.getByText(
        /^Your documents stay on this server, and no other user of this app can see them\./,
      ),
    ).toBeVisible();
    // The person who runs the server can read its database: "only you" would be false.
    expect(screen.queryByText(/only you/i)).toBeNull();
  });

  it(
    "asks again by itself while a document is being read, and only then",
    async () => {
      fakeSession(member);
      const state = fakeDocuments([
        fakeDocument({ name: "being.pdf", kind: "pdf", status: "pending" }),
        // Kept for a reader that does not exist yet: pending too, and never watched.
        fakeDocument({
          name: "scan.png",
          kind: "image",
          status: "pending",
          readable: false,
        }),
      ]);
      let asked = 0;
      server.use(
        // Counts, then lets the fake api answer.
        http.get("*/api/ingestion/documents", () => {
          asked += 1;
        }),
      );
      renderApp("/profile/fill?step=documents");
      await listed("being.pdf");
      expect(row("being.pdf").getByText("Being read")).toBeVisible();
      const first = asked;
      expect(first).toBeGreaterThan(0);

      // The parser finishes: nobody touches the page, and the list shows it.
      state.items = state.items.map((item) =>
        item.name === "being.pdf" ? { ...item, status: "done" } : item,
      );
      await waitFor(
        () => {
          expect(row("being.pdf").getByText("Ready")).toBeVisible();
        },
        { timeout: WATCH_MS + 2000 },
      );
      const second = asked;
      expect(second).toBeGreaterThan(first);

      // Nothing is being read any more: the file that waits does not keep the list asking.
      expect(row("scan.png").getByText("Waiting")).toBeVisible();
      await new Promise((resolve) => setTimeout(resolve, WATCH_MS + 500));
      expect(asked).toBe(second);
    },
    3 * WATCH_MS + 5000,
  );

  it("shows each document with its type, size, age and state", async () => {
    await open([
      fakeDocument({
        name: "dossier.md",
        size: 34_500,
        created_at: new Date(Date.now() - 3 * 3600 * 1000).toISOString(),
      }),
      fakeDocument({ name: "Pasted text", source: "pasted", size: 812 }),
    ]);
    expect(rows()).toHaveLength(2);
    expect(row("dossier.md").getByText(/Text, 34 KB, added/)).toBeVisible();
    expect(row("dossier.md").getByText("3 hours ago")).toBeVisible();
    expect(row("dossier.md").getByText("Ready")).toBeVisible();
    expect(
      row("Pasted text").getByText(/Pasted text, 812 bytes/),
    ).toBeVisible();
    expect(screen.getByText("2 documents, 34 KB of 100 MB used")).toBeVisible();
  });

  it("tells the truth about a file this version cannot read", async () => {
    // Every kind the api keeps has a reader since G8 (an image is read by the agent). What is
    // left without one is a type the api does not know: no reader is planned, so no promise.
    await open([
      fakeDocument({
        name: "scan.png",
        kind: "other",
        status: "pending",
        readable: false,
      }),
    ]);
    const cv = row("scan.png");
    expect(cv.getByText("Waiting")).toBeVisible();
    expect(
      cv.getByText(
        "This version cannot read this type of file. Your file is kept.",
      ),
    ).toBeVisible();
    // No text yet, so nothing to preview; it can still be deleted.
    expect(cv.queryByRole("button", { name: "Preview scan.png" })).toBeNull();
    expect(cv.getByRole("button", { name: "Delete scan.png" })).toBeVisible();
  });

  it("shows what a parser will report: being read, pages, a note, a failure", async () => {
    await open([
      fakeDocument({ name: "being.pdf", kind: "pdf", status: "pending" }),
      fakeDocument({
        name: "scan.pdf",
        kind: "pdf",
        status: "awaiting_transcription",
        note: "Pages 31 to 44 were not read.",
        pages: 44,
        pages_text: 12,
        pages_image: 3,
      }),
      fakeDocument({
        name: "locked.pdf",
        kind: "pdf",
        status: "failed",
        failed_reason:
          "This PDF is protected. Remove the protection and export it again.",
      }),
    ]);
    expect(row("being.pdf").getByText("Being read")).toBeVisible();
    const scan = row("scan.pdf");
    expect(scan.getByText("Waiting for the agent")).toBeVisible();
    expect(scan.getByText("Pages 31 to 44 were not read.")).toBeVisible();
    expect(scan.getByText("12 pages read as text, 3 as images")).toBeVisible();
    expect(
      scan.getByRole("button", { name: "Preview scan.pdf" }),
    ).toBeVisible();
    const locked = row("locked.pdf");
    expect(locked.getByText("Could not be read")).toBeVisible();
    expect(
      locked.getByText(
        "This PDF is protected. Remove the protection and export it again.",
      ),
    ).toBeVisible();
  });

  it("offers a retry when the list cannot be loaded", async () => {
    fakeSession(member);
    let fail = true;
    const state = fakeDocuments([fakeDocument({ name: "kept.md" })]);
    server.use(
      http.get("*/api/ingestion/documents", () =>
        fail
          ? HttpResponse.json({ detail: "database down" }, { status: 503 })
          : HttpResponse.json({
              items: state.items,
              usage: { bytes: 1, documents: 1 },
              limits: LIMITS,
            }),
      ),
    );
    const user = userEvent.setup();
    renderApp("/profile/fill?step=documents");
    expect(
      await screen.findByText("Your documents could not be loaded"),
    ).toBeVisible();
    // A sentence of ours: neither the server's words nor its status are for the user.
    expect(
      screen.getByText("The server could not answer. Try again in a moment."),
    ).toBeVisible();
    expect(screen.queryByText(/database down|The API|503/)).toBeNull();
    fail = false;
    await user.click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByText("kept.md")).toBeVisible();
  });
});

describe("documents: adding files", () => {
  it("adds a chosen file, which appears in the list as read", async () => {
    const { user, state, input } = await open();
    await user.upload(input, text("# Robin Example\n", "dossier.md"));
    await listed("dossier.md");
    expect(row("dossier.md").getByText("Ready")).toBeVisible();
    expect(state.uploads).toHaveLength(1);
    expect(state.uploads[0]?.filename).toBe("UTF-8''dossier.md");
    // The file left the queue, and the result was announced politely.
    await waitFor(() => {
      expect(
        screen.queryByRole("list", { name: "Files being added" }),
      ).toBeNull();
    });
    expect(announced()).toContain("dossier.md added.");
    expect(
      await screen.findByText(/^1 document, 16 bytes of 100 MB used$/),
    ).toBeVisible();
  });

  it("adds several files one after the other", async () => {
    const { user, state, input } = await open();
    await user.upload(input, [
      text("one", "1.txt"),
      text("two", "2.txt"),
      text("three", "3.txt"),
    ]);
    await waitFor(() => {
      expect(rows()).toHaveLength(3);
    });
    expect(state.uploads.map((upload) => upload.filename)).toEqual([
      "UTF-8''1.txt",
      "UTF-8''2.txt",
      "UTF-8''3.txt",
    ]);
  });

  it("keeps a PDF and says it is being read", async () => {
    const { user, input } = await open();
    await user.upload(input, new File(["%PDF-1.7 made up"], "cv.pdf"));
    await listed("cv.pdf");
    expect(row("cv.pdf").getByText("Being read")).toBeVisible();
  });

  it("shows the server's sentence for a type that is not read, until dismissed", async () => {
    const { user, input } = await open();
    await user.upload(input, new File(["GIF89a made up"], "anim.gif"));
    expect(
      await within(queue()).findByText(
        "GIF images cannot be read. Convert the image to PNG or JPEG and add it again.",
      ),
    ).toBeVisible();
    expect(within(queue()).getByText("Not added")).toBeVisible();
    // Said once to a screen reader: by the polite region, and by no alert beside it.
    expect(announced()).toContain(
      "anim.gif was not added. GIF images cannot be read.",
    );
    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.queryByRole("list", { name: "Your documents" })).toBeNull();
    await user.click(screen.getByRole("button", { name: "Dismiss anim.gif" }));
    expect(
      screen.queryByRole("list", { name: "Files being added" }),
    ).toBeNull();
  });

  it("refuses a file over the limit here, without sending it", async () => {
    const { user, state, input } = await open();
    const big = new File(["x"], "huge.pdf");
    Object.defineProperty(big, "size", { value: LIMITS.file_bytes + 1 });
    await user.upload(input, big);
    expect(
      await within(queue()).findByText("The file is larger than 20 MB."),
    ).toBeVisible();
    expect(state.uploads).toHaveLength(0);
  });

  it("refuses an empty file here", async () => {
    const { user, state, input } = await open();
    await user.upload(input, new File([], "empty.txt"));
    expect(
      await within(queue()).findByText("The file is empty."),
    ).toBeVisible();
    expect(state.uploads).toHaveLength(0);
  });

  it("never shows the words of a server error", async () => {
    const { user, input } = await open();
    server.use(
      http.post("*/api/ingestion/documents", () =>
        HttpResponse.json({ detail: "internal server error" }, { status: 500 }),
      ),
    );
    await user.upload(input, text("some words", "notes.md"));
    expect(
      await within(queue()).findByText(
        "The server could not take it. Try again in a moment.",
      ),
    ).toBeVisible();
    expect(screen.queryByText(/internal server error/i)).toBeNull();
    expect(announced()).not.toMatch(/internal server error/i);
  });

  it("says when the same file was already there, and adds no second row", async () => {
    const { user, input } = await open();
    await user.upload(input, text("same words", "first.txt"));
    await listed("first.txt");
    await user.upload(input, text("same words", "copy.txt"));
    expect(
      await within(queue()).findByText(
        "The same file is already in your documents, as first.txt.",
      ),
    ).toBeVisible();
    expect(within(queue()).getByText("Already there")).toBeVisible();
    expect(announced()).toContain(
      "copy.txt is already in your documents, as first.txt.",
    );
    expect(rows()).toHaveLength(1);
  });

  it("can choose the same file again after a refusal", async () => {
    const { user, state, input } = await open();
    const gif = () => new File(["GIF89a made up"], "anim.gif");
    await user.upload(input, gif());
    await within(queue()).findByText("Not added");
    await user.upload(input, gif());
    await waitFor(() => {
      expect(state.uploads).toHaveLength(2);
    });
  });

  it("shows the progress of a file on its way and lets it finish", async () => {
    const { user, input } = await open();
    let release: () => void = () => {};
    const held = new Promise<void>((resolve) => {
      release = resolve;
    });
    server.use(
      http.post("*/api/ingestion/documents", async () => {
        await held;
        return HttpResponse.json(
          { ...fakeDocument({ name: "slow.md" }), duplicate: false },
          { status: 201 },
        );
      }),
    );
    await user.upload(input, [
      text("slow", "slow.md"),
      text("next", "next.md"),
    ]);
    const bar = await screen.findByRole("progressbar", {
      name: "Sending slow.md",
    });
    expect(bar).toHaveAttribute("aria-valuemin", "0");
    expect(bar).toHaveAttribute("aria-valuemax", "100");
    expect(
      screen.getByRole("button", { name: "Cancel slow.md" }),
    ).toBeVisible();
    // The second file waits its turn, and can be taken back before it leaves.
    expect(within(queue()).getByText("Waiting")).toBeVisible();
    await user.click(screen.getByRole("button", { name: "Cancel next.md" }));
    expect(within(queue()).queryByText("next.md")).toBeNull();
    release();
    await waitFor(() => {
      expect(
        screen.queryByRole("list", { name: "Files being added" }),
      ).toBeNull();
    });
  });

  it("takes dropped files, and highlights the zone while they hover", async () => {
    const { state } = await open();
    const zone = screen
      .getByRole("heading", { name: "Drop files here, or choose them" })
      .closest("div") as HTMLElement;
    fireEvent.dragOver(zone, { dataTransfer: { files: [], items: [] } });
    expect(zone).toHaveAttribute("data-over", "true");
    fireEvent.dragLeave(zone, { relatedTarget: document.body });
    expect(zone).toHaveAttribute("data-over", "false");
    fireEvent.dragOver(zone, { dataTransfer: { files: [], items: [] } });
    fireEvent.drop(zone, {
      dataTransfer: { files: [text("dropped words", "dropped.md")], items: [] },
    });
    expect(zone).toHaveAttribute("data-over", "false");
    await listed("dropped.md");
    expect(state.uploads).toHaveLength(1);
  });

  it("does not lose the highlight when the pointer moves over a child of the zone", async () => {
    await open();
    const zone = screen
      .getByRole("heading", { name: "Drop files here, or choose them" })
      .closest("div") as HTMLElement;
    fireEvent.dragOver(zone, { dataTransfer: { files: [], items: [] } });
    // jsdom has no DragEvent: a mouse event carries the element the pointer went to.
    fireEvent(
      zone,
      new MouseEvent("dragleave", {
        bubbles: true,
        relatedTarget: screen.getByRole("button", { name: "Choose files" }),
      }),
    );
    expect(zone).toHaveAttribute("data-over", "true");
    fireEvent(
      zone,
      new MouseEvent("dragleave", {
        bubbles: true,
        relatedTarget: document.body,
      }),
    );
    expect(zone).toHaveAttribute("data-over", "false");
  });

  it("says that a dropped folder cannot be added, and takes the files beside it", async () => {
    const { state } = await open();
    const zone = screen
      .getByRole("heading", { name: "Drop files here, or choose them" })
      .closest("div") as HTMLElement;
    const file = text("beside the folder", "beside.md");
    fireEvent.drop(zone, {
      dataTransfer: {
        files: [file],
        items: [
          { kind: "string" },
          {
            kind: "file",
            webkitGetAsEntry: () => ({ isDirectory: true, name: "Documents" }),
          },
          {
            kind: "file",
            webkitGetAsEntry: () => ({ isDirectory: false, name: "beside.md" }),
            getAsFile: () => file,
          },
          { kind: "file", getAsFile: () => null },
        ],
      },
    });
    expect(
      await within(queue()).findByText(
        "A folder cannot be added. Open it and add the files inside.",
      ),
    ).toBeVisible();
    await listed("beside.md");
    expect(state.uploads).toHaveLength(1);
  });

  it("keeps the keyboard focus when a notice is dismissed", async () => {
    const { user, input } = await open();
    await user.upload(input, [
      new File([], "one.txt"),
      new File([], "two.txt"),
      new File([], "three.txt"),
    ]);
    const dismiss = (name: string) =>
      screen.getByRole("button", { name: `Dismiss ${name}` });
    // The row that takes the place of the one dismissed: the next one.
    dismiss("one.txt").focus();
    await user.keyboard("{Enter}");
    await waitFor(() => {
      expect(dismiss("two.txt")).toHaveFocus();
    });
    // The last row has no next one: the one before it.
    dismiss("three.txt").focus();
    await user.keyboard("{Enter}");
    await waitFor(() => {
      expect(dismiss("two.txt")).toHaveFocus();
    });
    // No row is left: the button that adds files.
    await user.keyboard("{Enter}");
    await waitFor(() => {
      expect(
        screen.getByRole("button", { name: "Choose files" }),
      ).toHaveFocus();
    });
    expect(
      screen.queryByRole("list", { name: "Files being added" }),
    ).toBeNull();
  });

  it("does not take the focus back from where the user went meanwhile", async () => {
    const { user, input } = await open();
    await user.upload(input, [
      new File([], "one.txt"),
      new File([], "two.txt"),
    ]);
    const box = screen.getByLabelText("Or paste a text");
    const one = screen.getByRole("button", { name: "Dismiss one.txt" });
    // A click that leaves the focus elsewhere, as a pointer does in some browsers.
    box.focus();
    fireEvent.click(one);
    await waitFor(() => {
      expect(
        screen.queryByRole("button", { name: "Dismiss one.txt" }),
      ).toBeNull();
    });
    expect(box).toHaveFocus();
  });

  it("opens the system's file picker from the button, by keyboard too", async () => {
    const { user, input } = await open();
    let opened = 0;
    input.addEventListener("click", () => {
      opened += 1;
    });
    const button = screen.getByRole("button", { name: "Choose files" });
    expect(button).toHaveAccessibleDescription(/up to 20 MB each/);
    // What is kept from an export is said where the export is offered.
    expect(button).toHaveAccessibleDescription(
      /From a LinkedIn export, your own profile files are the only ones read: connections, messages, contacts and invitations are ignored\./,
    );
    button.focus();
    await user.keyboard("{Enter}");
    expect(opened).toBe(1);
  });
});

describe("documents: pasting a text", () => {
  const box = () => screen.getByLabelText("Or paste a text");
  const add = () => screen.getByRole("button", { name: "Add this text" });

  it("adds a pasted text and empties the box", async () => {
    const { user, state } = await open();
    expect(add()).toBeDisabled();
    await user.click(box());
    await user.paste("Robin Example\nApplied AI engineer in Nantes");
    expect(screen.getByText("43 of 100,000 characters")).toBeVisible();
    await user.click(add());
    expect(await screen.findByText("Text added.")).toBeVisible();
    expect(box()).toHaveValue("");
    expect(state.pastes).toEqual([
      "Robin Example\nApplied AI engineer in Nantes",
    ]);
    expect(row("Pasted text").getByText("Ready")).toBeVisible();
  });

  it("counts an emoji as one character, as the server does", async () => {
    const { user } = await open();
    await user.click(box());
    await user.paste("\u{1f600}\u{1f680}ab");
    expect(screen.getByText("4 of 100,000 characters")).toBeVisible();
  });

  it("does not send a text over the limit, and says what to do instead", async () => {
    const { state } = await open();
    fireEvent.change(box(), {
      target: { value: "a".repeat(LIMITS.paste_characters + 1) },
    });
    expect(
      screen.getByText(
        "Too long: 100,001 of 100,000 characters. Save it as a text file and add the file instead.",
      ),
    ).toBeVisible();
    expect(add()).toBeDisabled();
    expect(box()).toHaveAttribute("aria-invalid", "true");
    fireEvent.submit(box().closest("form") as HTMLFormElement);
    expect(state.pastes).toEqual([]);
    fireEvent.change(box(), {
      target: { value: "a".repeat(LIMITS.paste_characters) },
    });
    expect(add()).toBeEnabled();
  });

  it("does not send a text of spaces only", async () => {
    const { user, state } = await open();
    await user.type(box(), "   ");
    expect(add()).toBeDisabled();
    expect(state.pastes).toEqual([]);
  });

  it("says when the same text is already there", async () => {
    const { user } = await open();
    const pasteIt = async () => {
      await user.click(box());
      await user.paste("the same words");
      await user.click(add());
      await waitFor(() => {
        expect(box()).toHaveValue("");
      });
    };
    await pasteIt();
    await pasteIt();
    expect(
      await screen.findByText(
        "This text is already in your documents, as Pasted text.",
      ),
    ).toBeVisible();
    expect(rows()).toHaveLength(1);
  });

  it("keeps the text and shows the server's sentence when it is refused", async () => {
    const { user } = await open();
    server.use(
      http.post("*/api/ingestion/documents/text", () =>
        HttpResponse.json(
          {
            detail:
              "Your documents would take more than the 100 MB you can keep here. Delete one to make room.",
          },
          { status: 413 },
        ),
      ),
    );
    await user.type(box(), "words to keep");
    await user.click(add());
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Your documents would take more than the 100 MB you can keep here. Delete one to make room.",
    );
    expect(box()).toHaveValue("words to keep");
    // Typing again clears the message.
    await user.type(box(), "!");
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("warns at once when the pasted text looks like it holds a key", async () => {
    const { user } = await open();
    await user.click(box());
    await user.paste("aws key AKIA and so on");
    await user.click(add());
    expect(
      await screen.findByText(
        "Text added. It looks like it holds a password or a key: check it in the list below.",
      ),
    ).toBeVisible();
    expect(
      row("Pasted text").getByRole("group", {
        name: "Possible secret in Pasted text",
      }),
    ).toBeVisible();
  });
});

describe("documents: the secret warning", () => {
  const flagged = () =>
    fakeDocument({ name: "env notes.txt", secret_warning: true });

  it("asks to look, then keeps the document when the user says it is fine", async () => {
    const { user, state } = await open([flagged()]);
    const warning = row("env notes.txt").getByRole("group", {
      name: "Possible secret in env notes.txt",
    });
    expect(
      within(warning).getByText("This may hold a password or an access key"),
    ).toBeVisible();
    expect(
      within(warning).getByText(/No agent reads this document until you say/),
    ).toBeVisible();
    // Not ready for an agent while the user has not looked.
    expect(row("env notes.txt").getByText("To check")).toBeVisible();
    await user.click(
      within(warning).getByRole("button", { name: "It is fine, keep it" }),
    );
    await waitFor(() => {
      expect(
        screen.queryByRole("group", { name: /Possible secret/ }),
      ).toBeNull();
    });
    expect(state.items[0]?.secret_confirmed_at).not.toBeNull();
    expect(row("env notes.txt").getByText("Ready")).toBeVisible();
  });

  it("is gone for a document already confirmed", async () => {
    await open([
      fakeDocument({
        name: "confirmed.txt",
        secret_warning: true,
        secret_confirmed_at: "2026-10-10T10:00:00Z",
      }),
    ]);
    expect(screen.queryByRole("group", { name: /Possible secret/ })).toBeNull();
  });

  it("says so when the confirmation fails, and keeps asking", async () => {
    const { user } = await open([flagged()]);
    server.use(
      http.post("*/api/ingestion/documents/:id/confirm", () =>
        HttpResponse.error(),
      ),
    );
    await user.click(
      screen.getByRole("button", { name: "It is fine, keep it" }),
    );
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "It did not go through. Check your connection and try again.",
    );
    expect(
      screen.getByRole("group", { name: "Possible secret in env notes.txt" }),
    ).toBeVisible();
  });

  it("offers to delete the document instead", async () => {
    const { user } = await open([flagged()]);
    await user.click(screen.getByRole("button", { name: "Delete it" }));
    expect(
      await screen.findByRole("alertdialog", { name: "Delete this document?" }),
    ).toBeVisible();
  });
});

describe("documents: the preview", () => {
  async function preview(
    content: string,
    overrides = {},
    page = 20_000,
    images: number[] = [],
  ) {
    const document = fakeDocument({ name: "dossier.md", ...overrides });
    const opened = await open([document]);
    opened.state.texts.set(document.id, content);
    opened.state.images.set(
      document.id,
      images.map((position) => ({ position, width: 1109, height: 1568 })),
    );
    opened.state.page = page;
    await opened.user.click(
      screen.getByRole("button", { name: "Preview dossier.md" }),
    );
    const dialog = await screen.findByRole("dialog", { name: "dossier.md" });
    return { ...opened, document, dialog: within(dialog) };
  }

  it("shows the text kept from the document", async () => {
    const { dialog, user } = await preview("# Robin Example\n\nNantes.");
    const region = await dialog.findByRole("region", {
      name: "Text of the document",
    });
    expect(region).toHaveTextContent("# Robin Example Nantes.");
    expect(region.querySelector("pre")?.textContent).toBe(
      "# Robin Example\n\nNantes.",
    );
    expect(region).toHaveAttribute("tabindex", "0"); // scrollable by keyboard
    expect(dialog.getByText("24 characters")).toBeVisible();
    expect(dialog.queryByRole("button", { name: "Show more" })).toBeNull();
    await user.keyboard("{Escape}");
    await waitFor(() => {
      expect(screen.queryByRole("dialog")).toBeNull();
    });
  });

  it("shows text as text: markup in a document is never run", async () => {
    const { dialog } = await preview("<img src=x onerror=alert(1)><b>bold</b>");
    const region = await dialog.findByRole("region");
    expect(region.querySelector("img")).toBeNull();
    expect(region.querySelector("b")).toBeNull();
    expect(region).toHaveTextContent("<img src=x onerror=alert(1)><b>bold</b>");
  });

  it("loads a long text a part at a time", async () => {
    const { dialog, user } = await preview("abcdefghij", {}, 4);
    expect(await dialog.findByText("abcd")).toBeVisible();
    expect(dialog.getByText("The first 4 of 10 characters")).toBeVisible();
    await user.click(dialog.getByRole("button", { name: "Show more" }));
    expect(await dialog.findByText("abcdefgh")).toBeVisible();
    await user.click(dialog.getByRole("button", { name: "Show more" }));
    expect(await dialog.findByText("abcdefghij")).toBeVisible();
    expect(dialog.getByText("10 characters")).toBeVisible();
    expect(dialog.queryByRole("button", { name: "Show more" })).toBeNull();
  });

  it("numbers the pages of a PDF and says which ones wait for the agent", async () => {
    const { dialog, document } = await preview(
      ["first page", "", "third page", ""].join(String.fromCharCode(12)),
      { kind: "pdf", status: "awaiting_transcription" },
      20_000,
      [1],
    );
    const pages = within(await dialog.findByRole("region")).getAllByRole(
      "listitem",
    );
    expect(pages.map((page) => page.textContent)).toEqual([
      "Page 1first page",
      "Page 2This page is an image. The agent will read it at the next run.",
      "Page 3third page",
      // A scanned page that got no image (over the cap, a size that is refused) waits for
      // nothing: the note of the document says it was left out.
      "Page 4This page holds no text.",
    ]);
    // The stored image of the page is shown, at its own address, with its size reserved.
    const image = dialog.getByRole("img", {
      name: "Page 2, kept as an image",
    });
    expect(image).toHaveAttribute(
      "src",
      `/api/ingestion/documents/${document.id}/images/1`,
    );
    expect(image).toHaveAttribute("width", "1109");
    expect(image).toHaveAttribute("height", "1568");
    expect(dialog.getAllByRole("img")).toHaveLength(1);
  });

  it("shows an uploaded image that waits for the agent", async () => {
    const { dialog, document } = await preview(
      "",
      { kind: "image", status: "awaiting_transcription" },
      20_000,
      [0],
    );
    expect(
      await dialog.findByText(
        "This page is an image. The agent will read it at the next run.",
      ),
    ).toBeVisible();
    expect(
      dialog.getByRole("img", { name: "Page 1, kept as an image" }),
    ).toHaveAttribute(
      "src",
      `/api/ingestion/documents/${document.id}/images/0`,
    );
    expect(dialog.queryByText("There is no text in this document.")).toBeNull();
  });

  it("says when a page of a document that was read holds no text", async () => {
    const { dialog } = await preview(
      ["first page", ""].join(String.fromCharCode(12)),
      { kind: "pdf" },
    );
    expect(await dialog.findByText("This page holds no text.")).toBeVisible();
  });

  it("says when there is no text at all", async () => {
    const { dialog } = await preview("", { status: "awaiting_transcription" });
    expect(
      await dialog.findByText("There is no text in this document."),
    ).toBeVisible();
    expect(dialog.getByText("0 characters")).toBeVisible();
  });

  it("offers a retry when the text cannot be loaded", async () => {
    const document = fakeDocument({ name: "dossier.md" });
    const { user, state } = await open([document]);
    await user.click(
      screen.getByRole("button", { name: "Preview dossier.md" }),
    );
    const dialog = within(await screen.findByRole("dialog"));
    expect(await dialog.findByRole("alert")).toHaveTextContent(
      "The text could not be loaded. This document has no text yet.",
    );
    state.texts.set(document.id, "now it is there");
    await user.click(dialog.getByRole("button", { name: "Retry" }));
    expect(await dialog.findByText("now it is there")).toBeVisible();
  });

  it("keeps what was loaded when the next part fails", async () => {
    const { dialog, user } = await preview("abcdefghij", {}, 4);
    await dialog.findByText("abcd");
    server.use(
      http.get("*/api/ingestion/documents/:id/text", () =>
        HttpResponse.error(),
      ),
    );
    await user.click(dialog.getByRole("button", { name: "Show more" }));
    expect(await dialog.findByRole("alert")).toHaveTextContent(
      "The rest could not be loaded.",
    );
    expect(dialog.getByText("abcd")).toBeVisible();
  });
});

describe("documents: deleting", () => {
  async function ask(name = "old cv.pdf") {
    const opened = await open([
      fakeDocument({ name }),
      fakeDocument({ name: "stays.md" }),
    ]);
    await opened.user.click(
      screen.getByRole("button", { name: `Delete ${name}` }),
    );
    const dialog = await screen.findByRole("alertdialog", {
      name: "Delete this document?",
    });
    return { ...opened, dialog: within(dialog) };
  }

  it("says what deleting means before it does anything", async () => {
    const { dialog, state } = await ask();
    expect(dialog.getByText("old cv.pdf")).toBeVisible();
    expect(
      dialog.getByText(/will be deleted for good, with its text/),
    ).toBeVisible();
    // True today: no number of days is promised while the server keeps its backups longer
    // than the 7 days the plan aims for (delete-document.tsx says when the sentence changes).
    expect(DELETE_PROMISE).toBe(
      "Gone from the app now. The server's backups still hold a copy for a while, until newer backups replace them.",
    );
    expect(DELETE_PROMISE).not.toMatch(/\d/);
    expect(dialog.getByText(DELETE_PROMISE)).toBeVisible();
    expect(state.deleted).toEqual([]);
  });

  it("keeps the document when the user says so", async () => {
    const { dialog, user, state } = await ask();
    await user.click(dialog.getByRole("button", { name: "Keep it" }));
    await waitFor(() => {
      expect(screen.queryByRole("alertdialog")).toBeNull();
    });
    expect(state.deleted).toEqual([]);
    expect(rows()).toHaveLength(2);
  });

  it("deletes it for good, says so, and puts the focus back on the list", async () => {
    const { dialog, user, state } = await ask();
    const gone = state.items[0]?.id;
    await user.click(dialog.getByRole("button", { name: "Delete for good" }));
    await waitFor(() => {
      expect(screen.queryByRole("alertdialog")).toBeNull();
    });
    expect(state.deleted).toEqual([gone]);
    expect(screen.queryByText("old cv.pdf")).toBeNull();
    expect(rows()).toHaveLength(1);
    expect(await screen.findByText("Document deleted.")).toBeVisible();
    await waitFor(() => {
      expect(
        screen.getByRole("heading", { level: 3, name: "Your documents" }),
      ).toHaveFocus();
    });
  });

  it("treats a document that was already deleted elsewhere as deleted", async () => {
    const { dialog, user, state } = await ask();
    state.items = state.items.filter((item) => item.name !== "old cv.pdf");
    await user.click(dialog.getByRole("button", { name: "Delete for good" }));
    await waitFor(() => {
      expect(screen.queryByRole("alertdialog")).toBeNull();
    });
    expect(screen.queryByText("old cv.pdf")).toBeNull();
  });

  it("shows the server's reason when it refuses, and the document stays", async () => {
    const { dialog, user } = await ask();
    server.use(
      http.delete("*/api/ingestion/documents/:id", () =>
        HttpResponse.json(
          {
            detail:
              "This document was read by a run and cannot be deleted yet.",
          },
          { status: 409 },
        ),
      ),
    );
    await user.click(dialog.getByRole("button", { name: "Delete for good" }));
    expect(await dialog.findByRole("alert")).toHaveTextContent(
      "This document was read by a run and cannot be deleted yet.",
    );
    await user.click(dialog.getByRole("button", { name: "Keep it" }));
    await waitFor(() => {
      expect(screen.queryByRole("alertdialog")).toBeNull();
    });
    expect(rows()).toHaveLength(2);
    // Asked again, the old refusal is not shown before anything was tried.
    await user.click(screen.getByRole("button", { name: "Delete old cv.pdf" }));
    const again = within(await screen.findByRole("alertdialog"));
    expect(again.queryByRole("alert")).toBeNull();
  });
});
