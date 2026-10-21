import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "@/lib/api";
import { fakeDocument } from "@/test/ingestion";

import * as api from "./documents-api";
import type { StoredDocument } from "./documents-api";
import { storedSentence, UploadQueue, type UploadsState } from "./use-uploads";

vi.mock("./documents-api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("./documents-api")>()),
  uploadDocument: vi.fn(),
}));

const LIMIT = 20 * 1024 * 1024;

/** One call of uploadDocument, held until the test settles it. */
interface Call {
  file: File;
  progress: (fraction: number) => void;
  signal: AbortSignal;
  resolve: (stored: StoredDocument) => void;
  reject: (error: unknown) => void;
}

let calls: Call[];

beforeEach(() => {
  calls = [];
  vi.mocked(api.uploadDocument).mockImplementation(
    (file, options = {}) =>
      new Promise<StoredDocument>((resolve, reject) => {
        const signal = options.signal ?? new AbortController().signal;
        signal.addEventListener("abort", () => {
          reject(new ApiError("aborted", "upload cancelled"));
        });
        calls.push({
          file,
          progress: options.onProgress ?? (() => {}),
          signal,
          resolve,
          reject,
        });
      }),
  );
});

const NOT_REPLACED =
  "This file is already among your documents and a run has read it, so this upload did not replace it. Delete that document first, then upload the file again.";
const file = (name: string, size = 10) =>
  new File([new Uint8Array(size)], name);
const stored = (overrides: Partial<StoredDocument> = {}): StoredDocument => ({
  ...fakeDocument(),
  duplicate: false,
  refused: null,
  ...overrides,
});
/** Let the promise chain of a settled upload run. */
const settle = () => new Promise((resolve) => setTimeout(resolve, 0));

function queue() {
  const states: UploadsState[] = [];
  const kept: StoredDocument[] = [];
  const cancelled = vi.fn();
  const created = new UploadQueue((state) => states.push(state), {
    onStored: (document) => kept.push(document),
    onCancelled: cancelled,
  });
  const last = () => states.at(-1) ?? { items: [], announcement: "" };
  const view = () => last().items.map((item) => `${item.name}: ${item.state}`);
  return { created, states, kept, cancelled, last, view };
}

describe("UploadQueue", () => {
  it("sends one file at a time, in the order they were added", async () => {
    const { created, view, last, kept } = queue();
    created.add([file("a.md"), file("b.md")], LIMIT);
    created.add([file("c.md")], LIMIT);
    expect(view()).toEqual(["a.md: uploading", "b.md: queued", "c.md: queued"]);
    expect(calls.map((call) => call.file.name)).toEqual(["a.md"]);
    expect(last().announcement).toBe("Sending a.md.");

    calls[0]?.resolve(stored({ name: "a.md" }));
    await settle();
    // A file that was added leaves the list: it is in the documents now.
    expect(view()).toEqual(["b.md: uploading", "c.md: queued"]);
    expect(calls.map((call) => call.file.name)).toEqual(["a.md", "b.md"]);
    expect(kept.map((document) => document.name)).toEqual(["a.md"]);

    calls[1]?.resolve(stored({ name: "b.md" }));
    await settle();
    calls[2]?.resolve(stored({ name: "c.md" }));
    await settle();
    expect(view()).toEqual([]);
    expect(last().announcement).toBe("c.md added.");
    expect(kept).toHaveLength(3);
  });

  it("reports the progress of the file on its way", async () => {
    const { created, last } = queue();
    created.add([file("a.md")], LIMIT);
    calls[0]?.progress(0.25);
    expect(last().items[0]).toMatchObject({
      state: "uploading",
      progress: 0.25,
    });
    calls[0]?.progress(1);
    expect(last().items[0]?.progress).toBe(1);
    calls[0]?.resolve(stored());
    await settle();
    expect(last().items).toEqual([]);
  });

  it("keeps a notice for a file that was already there", async () => {
    const { created, last, kept } = queue();
    created.add([file("copy.md")], LIMIT);
    calls[0]?.resolve(stored({ name: "original.md", duplicate: true }));
    await settle();
    expect(last().items).toEqual([
      expect.objectContaining({
        name: "copy.md",
        state: "duplicate",
        message: "The same file is already in your documents, as original.md.",
      }),
    ]);
    expect(last().announcement).toBe(
      "copy.md is already in your documents, as original.md.",
    );
    expect(kept).toHaveLength(1);
  });

  it("says why a file a run has read was not replaced", async () => {
    const { created, last, kept } = queue();
    created.add([file("cv.pdf")], LIMIT);
    calls[0]?.resolve(
      stored({ name: "cv.pdf", duplicate: true, refused: NOT_REPLACED }),
    );
    await settle();
    expect(last().items).toEqual([
      expect.objectContaining({
        name: "cv.pdf",
        state: "duplicate",
        message: NOT_REPLACED,
      }),
    ]);
    expect(last().announcement).toBe(`cv.pdf: ${NOT_REPLACED}`);
    expect(last().announcement).not.toContain("already in your documents.");
    expect(kept).toHaveLength(1);
  });

  it("keeps a refused file with the server's reason and goes on with the next", async () => {
    const { created, view, last, kept } = queue();
    created.add([file("a.gif"), file("b.md")], LIMIT);
    calls[0]?.reject(
      new ApiError("http", "x", {
        status: 415,
        body: { detail: "GIF images cannot be read." },
      }),
    );
    await settle();
    expect(view()).toEqual(["a.gif: failed", "b.md: uploading"]);
    expect(last().items[0]?.message).toBe("GIF images cannot be read.");
    calls[1]?.resolve(stored({ name: "b.md" }));
    await settle();
    expect(view()).toEqual(["a.gif: failed"]);
    expect(kept.map((document) => document.name)).toEqual(["b.md"]);
  });

  it("says what went wrong when the network fails", async () => {
    const { created, last } = queue();
    created.add([file("a.md")], LIMIT);
    calls[0]?.reject(new ApiError("network", "x"));
    await settle();
    expect(last().items[0]).toMatchObject({
      state: "failed",
      message: "It did not go through. Check your connection and try again.",
    });
    expect(last().announcement).toBe(
      "a.md was not added. It did not go through. Check your connection and try again.",
    );
  });

  it("refuses an empty file and one over the limit without sending them", () => {
    const { created, last } = queue();
    created.add(
      [file("empty.txt", 0), file("big.pdf", 1025), file("ok.md", 1024)],
      1024,
    );
    expect(calls.map((call) => call.file.name)).toEqual(["ok.md"]);
    expect(last().items.slice(0, 2)).toEqual([
      expect.objectContaining({
        name: "empty.txt",
        state: "failed",
        message: "The file is empty.",
      }),
      expect.objectContaining({
        name: "big.pdf",
        state: "failed",
        message: expect.stringContaining("The file is larger than") as string,
      }),
    ]);
  });

  it("names the real limit for a file that is too large", () => {
    const { created, last } = queue();
    const big = file("big.pdf");
    Object.defineProperty(big, "size", { value: LIMIT + 1 });
    const exact = file("exact.pdf");
    Object.defineProperty(exact, "size", { value: LIMIT });
    created.add([big, exact], LIMIT);
    expect(last().items[0]?.message).toBe("The file is larger than 20 MB.");
    expect(calls.map((call) => call.file.name)).toEqual(["exact.pdf"]);
  });

  it("refuses a folder with a sentence", () => {
    const { created, last } = queue();
    created.refuse("Documents", "A folder cannot be added.");
    expect(last().items).toEqual([
      expect.objectContaining({ name: "Documents", state: "failed" }),
    ]);
    expect(last().announcement).toBe(
      "Documents was not added. A folder cannot be added.",
    );
  });

  it("cancels a file that waits without touching the one on its way", async () => {
    const { created, view, last, cancelled } = queue();
    created.add([file("a.md"), file("b.md"), file("c.md")], LIMIT);
    const waiting = last().items[1]?.id ?? -1;
    created.remove(waiting);
    expect(view()).toEqual(["a.md: uploading", "c.md: queued"]);
    calls[0]?.resolve(stored());
    await settle();
    expect(calls.map((call) => call.file.name)).toEqual(["a.md", "c.md"]);
    // Nothing of it had left: there is nothing to look for on the server.
    expect(cancelled).not.toHaveBeenCalled();
  });

  it("cancels the file on its way, tells the page, and starts the next", async () => {
    const { created, view, last, kept, cancelled, states } = queue();
    created.add([file("a.md"), file("b.md")], LIMIT);
    // Every byte had left: the server may keep the file although the page stopped waiting.
    calls[0]?.progress(1);
    created.remove(last().items[0]?.id ?? -1);
    expect(calls[0]?.signal.aborted).toBe(true);
    await settle();
    expect(view()).toEqual(["b.md: uploading"]);
    expect(kept).toEqual([]);
    expect(cancelled).toHaveBeenCalledTimes(1);
    expect(states.map((state) => state.announcement)).toContain(
      "Stopped sending a.md.",
    );
  });

  it("dismisses a notice", async () => {
    const { created, last, cancelled } = queue();
    created.add([file("empty.txt", 0)], LIMIT);
    created.remove(last().items[0]?.id ?? -1);
    expect(last().items).toEqual([]);
    created.remove(12345); // nothing by that id: nothing happens
    await settle();
    expect(last().items).toEqual([]);
    expect(cancelled).not.toHaveBeenCalled();
  });

  it("stops everything when the page goes away, and says nothing more", async () => {
    const { created, states, kept, cancelled } = queue();
    created.add([file("a.md"), file("b.md")], LIMIT);
    const before = states.length;
    created.stop();
    expect(calls[0]?.signal.aborted).toBe(true);
    await settle();
    expect(calls).toHaveLength(1); // b.md never left
    expect(states).toHaveLength(before);
    created.add([file("late.md")], LIMIT);
    expect(calls).toHaveLength(1);
    expect(kept).toEqual([]);
    // Nobody is left to look at the list.
    expect(cancelled).not.toHaveBeenCalled();
  });

  it("does not report a file whose answer arrives after the page went away", async () => {
    const { created, kept } = queue();
    created.add([file("a.md")], LIMIT);
    const call = calls[0];
    // The server answered at the very moment the page closed: the abort comes too late.
    vi.mocked(api.uploadDocument).mockClear();
    created.stop();
    call?.resolve(stored());
    await settle();
    expect(kept).toEqual([]);
  });
});

describe("storedSentence", () => {
  it("says added, already there, or added with a warning", () => {
    expect(storedSentence("a.md", stored({ name: "a.md" }))).toBe(
      "a.md added.",
    );
    expect(
      storedSentence("a.md", stored({ name: "a.md", duplicate: true })),
    ).toBe("a.md is already in your documents.");
    expect(
      storedSentence("a.md", stored({ name: "b.md", duplicate: true })),
    ).toBe("a.md is already in your documents, as b.md.");
    expect(
      storedSentence(
        "a.md",
        stored({ name: "b.md", duplicate: true, refused: NOT_REPLACED }),
      ),
    ).toBe(`a.md: ${NOT_REPLACED}`);
    expect(storedSentence("a.md", stored({ secret_warning: true }))).toBe(
      "a.md added. It looks like it holds a password or a key: check it in the list below.",
    );
    expect(
      storedSentence(
        "a.md",
        stored({
          secret_warning: true,
          secret_confirmed_at: "2026-10-10T10:00:00Z",
        }),
      ),
    ).toBe("a.md added.");
  });
});
