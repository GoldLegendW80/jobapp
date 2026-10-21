/**
 * A fake /api/documents for tests: base CVs kept in memory with the api's rules (one default, the
 * oldest other promoted on a delete, at most `max`), a consent, and the documents behind them.
 * `requests` lists every call that reached it, so a test can assert what was (not) sent.
 */
import { http, HttpResponse } from "msw";

import type {
  BaseCv,
  Consent,
  DocumentOut,
  VersionSummary,
} from "@/features/documents/documents-api";

import { server } from "./server";

export const CONSENT_TEXT =
  "The app sends your profile and your base CV to Anthropic to write a CV.";

export function fakeConsent(overrides: Partial<Consent> = {}): Consent {
  return {
    version: 1,
    text: CONSENT_TEXT,
    accepted: false,
    accepted_at: null,
    model: "claude-opus-5-5",
    min_fit: 70,
    posting: { tokens: { low: 12_000, high: 20_000 }, cost: null },
    week: {
      tokens: { low: 60_000, high: 100_000 },
      cost: null,
      postings: 5,
      postings_30_days: 18,
    },
    cost_note:
      "On a Claude subscription, tokens count against your quota, not as money.",
    ...overrides,
  };
}

export function fakeVersion(
  overrides: Partial<VersionSummary> = {},
): VersionSummary {
  return {
    number: 1,
    origin: "generated",
    created_at: "2026-10-14T09:00:00Z",
    language: "en",
    template_slug: "classic",
    template_version: 1,
    fit: "one_page",
    fit_step: 0,
    pages: 1,
    has_pdf: true,
    has_preview: true,
    approved_at: null,
    frozen_at: null,
    frozen_reason: null,
    corrections_count: 0,
    unprintable_count: 0,
    ...overrides,
  };
}

export function fakeCv(overrides: Partial<BaseCv> = {}): BaseCv {
  return {
    id: "cv-1",
    name: "My CV",
    source: "profile",
    template_slug: "classic",
    language: "en",
    is_default: true,
    created_at: "2026-10-14T08:00:00Z",
    updated_at: "2026-10-14T09:00:00Z",
    last_version_at: "2026-10-14T09:00:00Z",
    render_state: "idle",
    generation: { id: "g-1", status: "done", wait_reason: null, error: null },
    ...overrides,
  };
}

export function fakeDocumentOf(
  cv: BaseCv,
  overrides: Partial<DocumentOut> = {},
): DocumentOut {
  return {
    id: cv.id,
    scope: "base",
    kind: "cv",
    posting_id: null,
    base_document_id: null,
    name: cv.name,
    language: cv.language,
    template_slug: cv.template_slug,
    draft: {},
    draft_revision: 1,
    draft_written_by: "writer",
    draft_updated_at: "2026-10-14T09:00:00Z",
    notes: [],
    render: { state: "idle", revision: 1, error: null, message: null },
    latest: cv.last_version_at === null ? null : fakeVersion(),
    ...overrides,
  };
}

export interface Call {
  method: string;
  path: string;
  body: unknown;
  headers: Headers;
}

export interface FakeDocuments {
  cvs: BaseCv[];
  consent: Consent;
  max: number;
  requests: Call[];
  /** Per document id, what GET /api/documents/{id} answers instead of the default. */
  documents: Map<string, DocumentOut>;
  versions: Map<string, VersionSummary[]>;
  corrections: Map<string, unknown[]>;
}

export function fakeDocuments(
  initial: {
    cvs?: BaseCv[];
    consent?: Partial<Consent>;
    max?: number;
  } = {},
): FakeDocuments {
  const state: FakeDocuments = {
    cvs: initial.cvs ?? [],
    consent: fakeConsent(initial.consent),
    max: initial.max ?? 10,
    requests: [],
    documents: new Map(),
    versions: new Map(),
    corrections: new Map(),
  };
  let counter = state.cvs.length;
  const note = async (request: Request): Promise<Call> => {
    const text = request.method === "GET" ? "" : await request.clone().text();
    const call: Call = {
      method: request.method,
      path: new URL(request.url).pathname,
      body: text === "" ? undefined : (JSON.parse(text) as unknown),
      headers: request.headers,
    };
    state.requests.push(call);
    return call;
  };
  const find = (id: string) => state.cvs.find((cv) => cv.id === id);

  server.use(
    http.get("*/api/documents/base-cvs", async ({ request }) => {
      await note(request);
      return HttpResponse.json({ base_cvs: state.cvs, max: state.max });
    }),
    http.post("*/api/documents/base-cvs", async ({ request }) => {
      const call = await note(request);
      const body = call.body as {
        from: string;
        name?: string;
        copy_of?: string;
      };
      if (state.cvs.length >= state.max)
        return HttpResponse.json(
          {
            detail:
              "You have 10 CVs, the most the app keeps. Delete one to add another.",
          },
          { status: 409 },
        );
      counter += 1;
      const source = find(body.copy_of ?? "");
      const made = fakeCv({
        id: `cv-${String(counter)}`,
        name:
          body.name ??
          (body.from === "copy" ? `${source?.name ?? "CV"} copy` : "My CV"),
        source: body.from === "copy" ? "built" : "profile",
        is_default: state.cvs.length === 0,
        last_version_at: body.from === "copy" ? "2026-10-14T09:00:00Z" : null,
        generation:
          body.from === "copy"
            ? null
            : {
                id: `g-${String(counter)}`,
                status: "queued",
                wait_reason: null,
                error: null,
              },
      });
      state.cvs.push(made);
      return HttpResponse.json(
        { base_cv: made },
        { status: body.from === "copy" ? 201 : 202 },
      );
    }),
    http.patch("*/api/documents/base-cvs/:id", async ({ request, params }) => {
      const call = await note(request);
      const cv = find(String(params.id));
      if (!cv)
        return HttpResponse.json({ detail: "Not found." }, { status: 404 });
      const body = call.body as { name?: string; default?: boolean };
      if (body.name !== undefined) cv.name = body.name;
      if (body.default === true) {
        for (const other of state.cvs) other.is_default = other.id === cv.id;
      }
      return HttpResponse.json(cv);
    }),
    http.delete("*/api/documents/base-cvs/:id", async ({ request, params }) => {
      await note(request);
      const cv = find(String(params.id));
      if (!cv)
        return HttpResponse.json({ detail: "Not found." }, { status: 404 });
      state.cvs = state.cvs.filter((one) => one.id !== cv.id);
      if (cv.is_default && state.cvs[0]) state.cvs[0].is_default = true;
      return new HttpResponse(null, { status: 204 });
    }),
    http.get("*/api/documents/consent", async ({ request }) => {
      await note(request);
      return HttpResponse.json(state.consent);
    }),
    http.post("*/api/documents/consent", async ({ request }) => {
      await note(request);
      state.consent = {
        ...state.consent,
        accepted: true,
        accepted_at: "2026-10-14T09:30:00Z",
      };
      return HttpResponse.json(state.consent);
    }),
    http.get(
      "*/api/documents/:id/versions/:number",
      async ({ request, params }) => {
        await note(request);
        const id = String(params.id);
        const summary = (state.versions.get(id) ?? []).find(
          (one) => one.number === Number(params.number),
        );
        if (!summary)
          return HttpResponse.json({ detail: "Not found." }, { status: 404 });
        return HttpResponse.json({
          ...summary,
          content: {},
          corrections: state.corrections.get(id) ?? [],
          unprintable: [],
        });
      },
    ),
    http.get("*/api/documents/:id/versions", async ({ request, params }) => {
      await note(request);
      const id = String(params.id);
      const cv = find(id);
      return HttpResponse.json(
        state.versions.get(id) ??
          (cv && cv.last_version_at !== null ? [fakeVersion()] : []),
      );
    }),
    http.get("*/api/documents/:id", async ({ request, params }) => {
      await note(request);
      const id = String(params.id);
      const set = state.documents.get(id);
      const cv = find(id);
      if (set) return HttpResponse.json(set);
      if (!cv)
        return HttpResponse.json({ detail: "Not found." }, { status: 404 });
      return HttpResponse.json(fakeDocumentOf(cv));
    }),
  );
  return state;
}
