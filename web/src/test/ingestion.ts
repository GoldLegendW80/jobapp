/**
 * A fake /api/ingestion/documents for tests: documents kept in memory and MSW handlers that
 * answer as the api does (api/app/ingestion/documents_router.py): the type from the first
 * bytes, text complete at once, a PDF kept and waiting, 415 for a GIF, 422 for an empty body,
 * 200 with `duplicate` for the same bytes again, a secret warning for a text holding "AKIA".
 * Every document is made up.
 */
import { http, HttpResponse } from "msw";

import type {
  IngestionDocument,
  Limits,
} from "@/features/ingestion/documents-api";
import type { Prompt } from "@/features/ingestion/prompt-api";

import { server } from "./server";

export const LIMITS: Limits = {
  file_bytes: 20 * 1024 * 1024,
  paste_characters: 100_000,
  text_characters: 500_000,
  user_bytes: 100 * 1024 * 1024,
  documents: 200,
};

export function fakeDocument(
  overrides: Partial<IngestionDocument> = {},
): IngestionDocument {
  return {
    id: crypto.randomUUID(),
    name: "notes.md",
    kind: "text",
    mime: "text/plain",
    size: 1234,
    status: "done",
    source: "parsed",
    readable: true,
    note: null,
    truncated: false,
    pages: null,
    pages_text: null,
    pages_image: null,
    failed_reason: null,
    secret_warning: false,
    secret_confirmed_at: null,
    created_at: new Date().toISOString(),
    ...overrides,
  };
}

export interface FakeDocuments {
  items: IngestionDocument[];
  /** The stored text of each document, by id. */
  texts: Map<string, string>;
  /** What each upload sent: the name header as received, and the body. */
  uploads: Array<{ filename: string | null; body: Uint8Array; type: string }>;
  pastes: string[];
  deleted: string[];
  /** Characters per page of the text route (the real one serves 20,000). */
  page: number;
}

const decoder = new TextDecoder("utf-8", { fatal: true });
const startsWith = (body: Uint8Array, magic: string) =>
  magic.split("").every((char, i) => body[i] === char.charCodeAt(0));

function nameOf(header: string | null): string {
  const match = /^UTF-8''(.*)$/i.exec(header ?? "");
  if (!match) return "Untitled";
  try {
    return decodeURIComponent(match[1] ?? "") || "Untitled";
  } catch {
    return "Untitled";
  }
}

const refusal = (status: number, detail: string) =>
  HttpResponse.json({ detail }, { status });

/** Install the handlers; returns the state, which a test may fill or inspect. */
export function fakeDocuments(
  initial: IngestionDocument[] = [],
): FakeDocuments {
  const state: FakeDocuments = {
    items: [...initial],
    texts: new Map(),
    uploads: [],
    pastes: [],
    deleted: [],
    page: 20_000,
  };
  const sameBytes = new Map<string, string>();

  const keep = (document: IngestionDocument, key: string, text?: string) => {
    const existing = sameBytes.get(key);
    const found = state.items.find((item) => item.id === existing);
    if (found) return HttpResponse.json({ ...found, duplicate: true });
    sameBytes.set(key, document.id);
    state.items.unshift(document);
    if (text !== undefined) state.texts.set(document.id, text);
    return HttpResponse.json(
      { ...document, duplicate: false },
      { status: 201 },
    );
  };

  server.use(
    http.get("*/api/ingestion/documents", () =>
      HttpResponse.json({
        items: state.items,
        usage: {
          bytes: state.items.reduce((sum, item) => sum + item.size, 0),
          documents: state.items.length,
        },
        limits: LIMITS,
      }),
    ),
    http.post("*/api/ingestion/documents", async ({ request }) => {
      const body = new Uint8Array(await request.arrayBuffer());
      const filename = request.headers.get("x-filename");
      state.uploads.push({
        filename,
        body,
        type: request.headers.get("content-type") ?? "",
      });
      const name = nameOf(filename);
      const key = Array.from(body).join(",");
      if (body.length === 0) return refusal(422, "The file is empty.");
      if (startsWith(body, "GIF8"))
        return refusal(
          415,
          "GIF images cannot be read. Convert the image to PNG or JPEG and add it again.",
        );
      if (startsWith(body, "%PDF-"))
        return keep(
          fakeDocument({
            name,
            kind: "pdf",
            mime: "application/pdf",
            size: body.length,
            // Since G2b a PDF has a reader: the api answers with the document still pending
            // and the worker reads it a moment later.
            status: "pending",
            readable: true,
          }),
          key,
        );
      let text: string;
      try {
        text = decoder.decode(body);
      } catch {
        return refusal(
          415,
          "This type of file cannot be read. Accepted: PDF, Word (DOCX), plain text, Markdown, JSON, CSV, PNG, JPEG and the LinkedIn export (ZIP).",
        );
      }
      return keep(
        fakeDocument({
          name,
          size: body.length,
          secret_warning: text.includes("AKIA"),
        }),
        key,
        text,
      );
    }),
    http.post("*/api/ingestion/documents/text", async ({ request }) => {
      const { text } = (await request.json()) as { text: string };
      state.pastes.push(text);
      if (text.trim() === "") return refusal(422, "There is no text to add.");
      return keep(
        fakeDocument({
          name: "Pasted text",
          source: "pasted",
          size: new TextEncoder().encode(text).length,
          secret_warning: text.includes("AKIA"),
        }),
        `paste:${text}`,
        text,
      );
    }),
    http.get("*/api/ingestion/documents/:id/text", ({ params, request }) => {
      const id = String(params.id);
      if (!state.items.some((item) => item.id === id))
        return refusal(404, "Document not found.");
      const text = state.texts.get(id);
      if (text === undefined)
        return refusal(409, "This document has no text yet.");
      const characters = Array.from(text);
      const offset = Number(new URL(request.url).searchParams.get("offset"));
      const piece = characters.slice(offset, offset + state.page);
      const end = offset + piece.length;
      return HttpResponse.json({
        text: piece.join(""),
        offset,
        next_offset: end < characters.length ? end : null,
        total: characters.length,
      });
    }),
    http.post("*/api/ingestion/documents/:id/confirm", ({ params }) => {
      const found = state.items.find((item) => item.id === params.id);
      if (!found) return refusal(404, "Document not found.");
      found.secret_confirmed_at ??= new Date().toISOString();
      return HttpResponse.json(found);
    }),
    http.delete("*/api/ingestion/documents/:id", ({ params }) => {
      const id = String(params.id);
      if (!state.items.some((item) => item.id === id))
        return refusal(404, "Document not found.");
      state.items = state.items.filter((item) => item.id !== id);
      state.deleted.push(id);
      return new HttpResponse(null, { status: 204 });
    }),
  );
  return state;
}

/** What GET /api/ingestion/prompt answers in tests: two short texts, told apart by their first line. */
export const PROMPT: Prompt = {
  version: 1,
  sha256: "ab".repeat(32),
  output_folder: "~/jobapp-dossier",
  claude_code:
    "You are Claude Code, running in a terminal on my own computer.\n\nMy name: «Zoë Ångström»\n\nWrite exactly one file: `~/jobapp-dossier/dossier.md`.\n",
  codex:
    "You are Codex, running in a terminal on my own computer.\n\nMy name: «Zoë Ångström»\n\nWrite exactly one file: `~/jobapp-dossier/dossier.md`.\n",
};

/** Install the handler of the prompt; the answer says how many times it was asked, and where. */
export function fakePrompt(prompt: Prompt = PROMPT): {
  count: number;
  paths: string[];
} {
  const calls = { count: 0, paths: [] as string[] };
  server.use(
    http.get("*/api/ingestion/prompt", ({ request }) => {
      calls.count += 1;
      calls.paths.push(new URL(request.url).pathname);
      return HttpResponse.json(prompt);
    }),
  );
  return calls;
}
