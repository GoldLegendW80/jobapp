import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";

import { ApiError } from "@/lib/api";
import { fakeDocument, fakeDocuments } from "@/test/ingestion";
import { server } from "@/test/server";

import {
  confirmDocument,
  countCharacters,
  DEFAULT_LIMITS,
  deleteDocument,
  failureSentence,
  filenameHeader,
  documentImageUrl,
  getDocumentText,
  listDocuments,
  loadFailureSentence,
  megabytes,
  PASTE_TIMEOUT_MS,
  pasteDocument,
  uploadDocument,
} from "./documents-api";

const http413 = (body: unknown) =>
  new ApiError("http", "x", { status: 413, body });

describe("filenameHeader", () => {
  it("percent encodes the name after UTF-8''", () => {
    expect(filenameHeader("notes.md")).toBe("UTF-8''notes.md");
    expect(filenameHeader("CV Robin Example.pdf")).toBe(
      "UTF-8''CV%20Robin%20Example.pdf",
    );
    expect(filenameHeader("r\u00e9sum\u00e9 \u65e5\u672c.md")).toBe(
      "UTF-8''r%C3%A9sum%C3%A9%20%E6%97%A5%E6%9C%AC.md",
    );
    expect(filenameHeader("cv \u{1f600}.png")).toBe(
      "UTF-8''cv%20%F0%9F%98%80.png",
    );
  });

  it("also encodes the characters RFC 5987 does not allow bare", () => {
    expect(filenameHeader("it's (final)*!.txt")).toBe(
      "UTF-8''it%27s%20%28final%29%2A%21.txt",
    );
    expect(filenameHeader("../a/b\\c\n.txt")).toBe(
      "UTF-8''..%2Fa%2Fb%5Cc%0A.txt",
    );
  });

  it("cuts a long name at 200 characters, never inside one", () => {
    const header = filenameHeader("\u{1f600}".repeat(500)) ?? "";
    expect(decodeURIComponent(header.slice("UTF-8''".length))).toBe(
      "\u{1f600}".repeat(200),
    );
    expect(filenameHeader("x".repeat(1000))).toBe(`UTF-8''${"x".repeat(200)}`);
  });

  it("gives up on a name that cannot be encoded", () => {
    expect(filenameHeader("broken \ud83d name.txt")).toBeUndefined();
  });

  it("is empty for an empty name, which the server calls Untitled", () => {
    expect(filenameHeader("")).toBe("UTF-8''");
  });
});

describe("countCharacters", () => {
  it("counts characters as the server does, an emoji as one", () => {
    expect(countCharacters("")).toBe(0);
    expect(countCharacters("abc")).toBe(3);
    expect(countCharacters("\u00e9\u65e5")).toBe(2);
    expect(countCharacters("\u{1f600}")).toBe(1);
    expect(countCharacters("a\u{1f600}b\u{1f680}")).toBe(4);
    expect(countCharacters("line\nbreak")).toBe(10);
  });
});

describe("megabytes", () => {
  it("shows 20 MiB as 20", () => {
    expect(megabytes(20 * 1024 * 1024)).toBe(20);
    expect(megabytes(DEFAULT_LIMITS.user_bytes)).toBe(100);
  });
});

describe("PASTE_TIMEOUT_MS", () => {
  it("outlasts the 35 s a paste may wait for its user's turn on the server", () => {
    // SLOT_WAIT_S in api/app/ingestion/documents_router.py: the server answers first.
    expect(PASTE_TIMEOUT_MS).toBeGreaterThan(35_000);
  });
});

describe("failureSentence", () => {
  it("uses the server's sentence when there is one", () => {
    expect(
      failureSentence(http413({ detail: "The file is larger than 20 MB." })),
    ).toBe("The file is larger than 20 MB.");
    expect(
      failureSentence(
        new ApiError("http", "x", {
          status: 415,
          body: { detail: "GIF images cannot be read." },
        }),
      ),
    ).toBe("GIF images cannot be read.");
  });

  it("names the limit when a proxy refused the body in plain text", () => {
    expect(failureSentence(http413("413 Request Entity Too Large"))).toBe(
      "The file is larger than 20 MB.",
    );
    expect(failureSentence(http413(undefined), 5 * 1024 * 1024)).toBe(
      "The file is larger than 5 MB.",
    );
  });

  it("shows the server's words only where they were written for the user", () => {
    const answered = (status: number, detail: string) =>
      failureSentence(new ApiError("http", "x", { status, body: { detail } }));
    for (const status of [400, 404, 408, 409, 413, 415, 422, 429, 503])
      expect(answered(status, "A sentence for the user.")).toBe(
        "A sentence for the user.",
      );
    // The api's catch all, the CSRF refusal, a proxy: never shown.
    expect(answered(500, "internal server error")).toBe(
      "The server could not take it. Try again in a moment.",
    );
    expect(answered(403, "missing X-Requested-With header")).toBe(
      "The server could not take it. Try again in a moment.",
    );
    expect(answered(502, "bad gateway")).toBe(
      "The server could not take it. Try again in a moment.",
    );
    expect(answered(401, "not authenticated")).toBe(
      "Your session has ended. Sign in again.",
    );
  });

  it("has a sentence for every other failure, none with the status code", () => {
    const cases: Array<[unknown, string]> = [
      [
        new ApiError("http", "x", { status: 500, body: "boom" }),
        "The server could not take it. Try again in a moment.",
      ],
      [
        new ApiError("network", "x"),
        "It did not go through. Check your connection and try again.",
      ],
      [
        new ApiError("aborted", "x"),
        "It did not go through. Check your connection and try again.",
      ],
      [
        new ApiError("timeout", "x"),
        "The server took too long to answer. Try again.",
      ],
      [
        new ApiError("parse", "x"),
        "The server sent an answer this page cannot read. Reload the page.",
      ],
      [new Error("anything"), "Something unexpected went wrong. Try again."],
    ];
    for (const [error, sentence] of cases) {
      expect(failureSentence(error)).toBe(sentence);
      expect(sentence).not.toMatch(/\d{3}| - |\u2014|\u2013/);
    }
  });
});

describe("loadFailureSentence", () => {
  it("never shows the server's words, its status, or the word API", () => {
    const cases: Array<[unknown, string]> = [
      [
        new ApiError("http", "x", {
          status: 500,
          body: { detail: "internal server error" },
        }),
        "The server could not answer. Try again in a moment.",
      ],
      [
        new ApiError("http", "x", {
          status: 503,
          body: { detail: "database unavailable" },
        }),
        "The server could not answer. Try again in a moment.",
      ],
      [
        new ApiError("http", "x", { status: 401, body: { detail: "nope" } }),
        "Your session has ended. Sign in again.",
      ],
      [
        new ApiError("network", "x"),
        "The server could not be reached. Check your connection and try again.",
      ],
      [
        new ApiError("aborted", "x"),
        "The server could not be reached. Check your connection and try again.",
      ],
      [
        new ApiError("timeout", "x"),
        "The server took too long to answer. Try again.",
      ],
      [
        new ApiError("parse", "x"),
        "The server sent an answer this page cannot read. Reload the page.",
      ],
      [new Error("anything"), "Something unexpected went wrong. Try again."],
    ];
    for (const [error, sentence] of cases) {
      expect(loadFailureSentence(error)).toBe(sentence);
      expect(sentence).not.toMatch(/\d{3}|API| - |\u2014|\u2013/);
    }
  });
});

describe("calls", () => {
  it("lists the documents with what they take and the limits", async () => {
    const state = fakeDocuments([fakeDocument({ name: "a.md", size: 10 })]);
    const list = await listDocuments();
    expect(list.items.map((item) => item.name)).toEqual(["a.md"]);
    expect(list.usage).toEqual({ bytes: 10, documents: 1 });
    expect(list.limits).toEqual(DEFAULT_LIMITS);
    expect(state.items).toHaveLength(1);
  });

  it("rejects a list of another shape", async () => {
    server.use(
      http.get("*/api/ingestion/documents", () =>
        HttpResponse.json({ items: [{ id: 1 }] }),
      ),
    );
    await expect(listDocuments()).rejects.toMatchObject({ kind: "parse" });
  });

  it("uploads the file as the raw body with its name in X-Filename", async () => {
    const state = fakeDocuments();
    const file = new File(["# Robin Example\n"], "r\u00e9sum\u00e9.md", {
      type: "text/markdown",
    });
    const stored = await uploadDocument(file);
    expect(stored).toMatchObject({
      name: "r\u00e9sum\u00e9.md",
      kind: "text",
      status: "done",
      duplicate: false,
    });
    const [sent] = state.uploads;
    expect(sent?.filename).toBe("UTF-8''r%C3%A9sum%C3%A9.md");
    expect(new TextDecoder().decode(sent?.body)).toBe("# Robin Example\n");
    // The declared type is never the file's own: the server reads the bytes.
    expect(sent?.type).toBe("application/octet-stream");
  });

  it("says when the same file was already there", async () => {
    fakeDocuments();
    const file = () => new File(["same bytes"], "a.txt");
    const first = await uploadDocument(file());
    const again = await uploadDocument(file());
    expect(again).toMatchObject({ id: first.id, duplicate: true });
  });

  it("sends a name the browser already made well formed", async () => {
    // A File's name is a well formed string: half a surrogate pair became U+FFFD in it.
    const state = fakeDocuments();
    const stored = await uploadDocument(new File(["text"], "bad \ud83d.txt"));
    expect(state.uploads[0]?.filename).toBe("UTF-8''bad%20%EF%BF%BD.txt");
    expect(stored.name).toBe("bad \ufffd.txt");
  });

  it("fails with the server's refusal", async () => {
    fakeDocuments();
    await expect(
      uploadDocument(new File(["GIF89a..."], "a.gif")),
    ).rejects.toMatchObject({ kind: "http", status: 415 });
  });

  it("pastes a text", async () => {
    const state = fakeDocuments();
    const stored = await pasteDocument("Robin Example, Nantes");
    expect(stored).toMatchObject({ name: "Pasted text", source: "pasted" });
    expect(state.pastes).toEqual(["Robin Example, Nantes"]);
  });

  it("reads the text a page at a time", async () => {
    const document = fakeDocument();
    const state = fakeDocuments([document]);
    state.texts.set(document.id, "abcdefghij");
    state.page = 4;
    await expect(getDocumentText(document.id, 0)).resolves.toEqual({
      text: "abcd",
      offset: 0,
      next_offset: 4,
      total: 10,
      images: [],
    });
    // The pages that wait as images come with the text, each with its place and its size.
    const stored = [{ position: 2, width: 1109, height: 1568 }];
    state.images.set(document.id, stored);
    await expect(getDocumentText(document.id, 8)).resolves.toEqual({
      text: "ij",
      offset: 8,
      next_offset: null,
      total: 10,
      images: stored,
    });
    expect(documentImageUrl("a b/c", 2)).toBe(
      "/api/ingestion/documents/a%20b%2Fc/images/2",
    );
  });

  it("asks for 20,000 characters from the offset", async () => {
    let asked = "";
    server.use(
      http.get("*/api/ingestion/documents/:id/text", ({ request }) => {
        asked = new URL(request.url).search;
        return HttpResponse.json({
          text: "",
          offset: 40_000,
          next_offset: null,
          total: 0,
        });
      }),
    );
    await getDocumentText("some id/with a slash", 40_000);
    expect(asked).toBe("?offset=40000&limit=20000");
  });

  it("confirms a document", async () => {
    const document = fakeDocument({ secret_warning: true });
    fakeDocuments([document]);
    const confirmed = await confirmDocument(document.id);
    expect(confirmed.secret_confirmed_at).not.toBeNull();
  });

  it("deletes a document, and one that is already gone counts as deleted", async () => {
    const document = fakeDocument();
    const state = fakeDocuments([document]);
    await expect(deleteDocument(document.id)).resolves.toBeUndefined();
    expect(state.deleted).toEqual([document.id]);
    await expect(deleteDocument(document.id)).resolves.toBeUndefined();
  });

  it("does not hide another failure of a deletion", async () => {
    server.use(
      http.delete("*/api/ingestion/documents/:id", () =>
        HttpResponse.json(
          { detail: "This document was read by a run." },
          { status: 409 },
        ),
      ),
    );
    await expect(deleteDocument("x")).rejects.toMatchObject({ status: 409 });
  });
});
