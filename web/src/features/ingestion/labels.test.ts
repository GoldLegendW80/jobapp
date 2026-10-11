import { describe, expect, it } from "vitest";

import { fakeDocument } from "@/test/ingestion";

import {
  hasText,
  kindLabel,
  needsConfirmation,
  pagesLabel,
  sizeLabel,
  statusView,
} from "./labels";

describe("kindLabel", () => {
  it("names each kind in plain words", () => {
    expect(kindLabel(fakeDocument())).toBe("Text");
    expect(kindLabel(fakeDocument({ source: "pasted" }))).toBe("Pasted text");
    expect(kindLabel(fakeDocument({ kind: "pdf" }))).toBe("PDF");
    expect(kindLabel(fakeDocument({ kind: "archive" }))).toBe("Word or ZIP");
    expect(kindLabel(fakeDocument({ kind: "image" }))).toBe("Image");
  });
});

describe("sizeLabel", () => {
  it("is short and never shows a false precision", () => {
    expect(sizeLabel(1)).toBe("1 byte");
    expect(sizeLabel(812)).toBe("812 bytes");
    expect(sizeLabel(1024)).toBe("1 KB");
    expect(sizeLabel(34_500)).toBe("34 KB");
    expect(sizeLabel(1024 * 1024)).toBe("1.0 MB");
    expect(sizeLabel(3.44 * 1024 * 1024)).toBe("3.4 MB");
    expect(sizeLabel(20 * 1024 * 1024)).toBe("20 MB");
  });
});

describe("statusView", () => {
  it("a document whose text is there is ready", () => {
    expect(statusView(fakeDocument())).toEqual({
      label: "Ready",
      tone: "done",
    });
    expect(
      statusView(
        fakeDocument({
          secret_warning: true,
          secret_confirmed_at: "2026-10-10T10:00:00Z",
        }),
      ),
    ).toEqual({ label: "Ready", tone: "done" });
  });

  it("a document the user still has to look at is not ready yet", () => {
    expect(statusView(fakeDocument({ secret_warning: true }))).toEqual({
      label: "To check",
      tone: "waiting",
    });
  });

  it("a file a parser is working on is being read", () => {
    expect(
      statusView(fakeDocument({ kind: "pdf", status: "pending" })),
    ).toEqual({ label: "Being read", tone: "working" });
  });

  it("a file kept for a reader that does not exist yet says so", () => {
    const view = (kind: "pdf" | "archive" | "image" | "text") =>
      statusView(fakeDocument({ kind, status: "pending", readable: false }));
    expect(view("pdf")).toEqual({
      label: "Waiting",
      tone: "waiting",
      detail:
        "This version cannot read PDF files yet. Your file is kept, and it will be read as soon as it can.",
    });
    expect(view("archive").detail).toContain("Word files and archives");
    expect(view("image").detail).toContain("images");
    expect(view("text").detail).toContain("text files");
  });

  it("pages kept as images wait for the agent", () => {
    expect(
      statusView(fakeDocument({ status: "awaiting_transcription" })),
    ).toEqual({
      label: "Waiting for the agent",
      tone: "waiting",
      detail: "Its pages are images. The agent will read them at the next run.",
    });
  });

  it("a failed document gives the server's reason, or a plain one", () => {
    expect(
      statusView(
        fakeDocument({
          status: "failed",
          failed_reason: "This PDF is protected.",
        }),
      ),
    ).toEqual({
      label: "Could not be read",
      tone: "failed",
      detail: "This PDF is protected.",
    });
    expect(statusView(fakeDocument({ status: "failed" })).detail).toBe(
      "This file could not be read.",
    );
  });
});

describe("pagesLabel", () => {
  const pages = (asText: number | null, asImages: number | null) =>
    pagesLabel(fakeDocument({ pages_text: asText, pages_image: asImages }));

  it("says nothing while no parser counted the pages", () => {
    expect(pages(null, null)).toBeUndefined();
    expect(pages(0, 0)).toBeUndefined();
  });

  it("counts the pages read as text and those kept as images", () => {
    expect(pages(12, 3)).toBe("12 pages read as text, 3 as images");
    expect(pages(1, 1)).toBe("1 page read as text, 1 as an image");
    expect(pages(12, 0)).toBe("12 pages read as text");
    expect(pages(12, null)).toBe("12 pages read as text");
    expect(pages(0, 2)).toBe("2 pages kept as images");
    expect(pages(null, 1)).toBe("1 page kept as an image");
  });
});

describe("needsConfirmation and hasText", () => {
  it("a flagged document needs a look until it is confirmed", () => {
    expect(needsConfirmation(fakeDocument())).toBe(false);
    expect(needsConfirmation(fakeDocument({ secret_warning: true }))).toBe(
      true,
    );
    expect(
      needsConfirmation(
        fakeDocument({
          secret_warning: true,
          secret_confirmed_at: "2026-10-10T10:00:00Z",
        }),
      ),
    ).toBe(false);
  });

  it("only a document with a stored text has a preview", () => {
    expect(hasText(fakeDocument())).toBe(true);
    expect(hasText(fakeDocument({ status: "awaiting_transcription" }))).toBe(
      true,
    );
    expect(hasText(fakeDocument({ status: "pending" }))).toBe(false);
    expect(hasText(fakeDocument({ status: "failed" }))).toBe(false);
  });
});
