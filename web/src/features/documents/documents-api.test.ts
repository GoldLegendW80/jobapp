import { http, HttpResponse, type JsonBodyType } from "msw";
import { describe, expect, it } from "vitest";

import {
  CONSENT_TEXT,
  fakeConsent,
  fakeCv,
  fakeDocumentOf,
  fakeVersion,
} from "@/test/documents";
import { server } from "@/test/server";
import { ApiError } from "@/lib/api";

import * as api from "./documents-api";

const cv = fakeCv();
const document = fakeDocumentOf(cv);
const generation = {
  id: "g-1",
  status: "queued",
  wait_reason: null,
  error: null,
};
const spend = {
  day: "2026-10-14",
  auto: true,
  min_fit: 70,
  daily_ceiling: 100,
  written_today: 3,
  waiting: 0,
  postings_30_days: 12,
  to_write: 2,
  to_write_estimate: { tokens: { low: 1, high: 2 }, cost: null },
  priced: false,
  cost_note: "Quota.",
  today: { input: 1, output: 2, cost_usd: "0.10" },
  last_30_days: { input: 3, output: 4, cost_usd: "1.00" },
};
const template = {
  slug: "classic",
  version: 1,
  user: false,
  name: { en: "Classic", fr: "Classique" },
  description: { en: "x", fr: "x" },
  tags: ["ats"],
  photo: "none",
  accent: null,
  kinds: ["cv", "letter"],
  thumbnails: { cv: "/api/document-templates/classic/thumbnail?kind=cv&v=1" },
};
const message = {
  id: 1,
  channel: "content",
  role: "user",
  text: "Shorter",
  generation_id: "g-1",
  version_number: null,
  at: "2026-10-14T09:00:00Z",
};
const detail = {
  ...generation,
  document_id: "d1",
  mode: "chat",
  trigger: "chat",
  outcome: null,
  version_number: null,
  corrections: 0,
  created_at: "2026-10-14T09:00:00Z",
  started_at: null,
  finished_at: null,
};

interface Case {
  name: string;
  run: () => Promise<unknown>;
  method: string;
  /** Path and query, as the api sees them. */
  url: string;
  body?: unknown;
  headers?: Record<string, string>;
  reply?: unknown;
  status?: number;
}

/** One case per function: the method, the path, the body and the headers of the spec's table. */
const CASES: Case[] = [
  {
    name: "listBaseCvs",
    run: () => api.listBaseCvs(),
    method: "GET",
    url: "/api/documents/base-cvs",
    reply: { base_cvs: [cv], max: 10 },
  },
  {
    name: "createBaseCv",
    run: () => api.createBaseCv({ from: "copy", copy_of: "a/b" }),
    method: "POST",
    url: "/api/documents/base-cvs",
    body: { from: "copy", copy_of: "a/b" },
    reply: { base_cv: cv },
    status: 201,
  },
  {
    name: "patchBaseCv",
    run: () => api.patchBaseCv("c1", { default: true }),
    method: "PATCH",
    url: "/api/documents/base-cvs/c1",
    body: { default: true },
    reply: cv,
  },
  {
    name: "deleteBaseCv",
    run: () => api.deleteBaseCv("c1"),
    method: "DELETE",
    url: "/api/documents/base-cvs/c1",
    status: 204,
  },
  {
    name: "captureDesign",
    run: () => api.captureDesign("d1"),
    method: "POST",
    url: "/api/documents/d1/design/capture",
    reply: generation,
    status: 202,
  },
  {
    name: "getPostingDocuments",
    run: () => api.getPostingDocuments("j1"),
    method: "GET",
    url: "/api/jobs/j1/documents",
    reply: {
      documents: [{ ...document, approved: null, sent: [], generation: null }],
    },
  },
  {
    name: "writeNow",
    run: () =>
      api.writeNow("j1", { kinds: ["cv", "letter"], base_document_id: "b1" }),
    method: "POST",
    url: "/api/jobs/j1/documents/write",
    body: { kinds: ["cv", "letter"], base_document_id: "b1" },
    reply: {
      generations: [
        { id: "g", kind: "cv", status: "queued", wait_reason: null },
      ],
    },
    status: 202,
  },
  {
    name: "getDocument",
    run: () => api.getDocument("d1"),
    method: "GET",
    url: "/api/documents/d1",
    reply: document,
  },
  {
    name: "saveDraft",
    run: () => api.saveDraft("d1", { headline: "x" }, 4),
    method: "PUT",
    url: "/api/documents/d1/draft",
    body: { headline: "x" },
    headers: { "if-match": "4" },
    reply: document,
  },
  {
    name: "patchDocument",
    run: () =>
      api.patchDocument("d1", { language: "fr", template_slug: "compact" }),
    method: "PATCH",
    url: "/api/documents/d1",
    body: { language: "fr", template_slug: "compact" },
    reply: document,
  },
  {
    name: "renderDocument",
    run: () => api.renderDocument("d1"),
    method: "POST",
    url: "/api/documents/d1/render",
    reply: { render: document.render },
    status: 202,
  },
  {
    name: "fitDocument",
    run: () => api.fitDocument("d1"),
    method: "POST",
    url: "/api/documents/d1/fit",
    reply: generation,
    status: 202,
  },
  {
    name: "listVersions",
    run: () => api.listVersions("d1"),
    method: "GET",
    url: "/api/documents/d1/versions",
    reply: [fakeVersion()],
  },
  {
    name: "getVersion",
    run: () => api.getVersion("d1", 3),
    method: "GET",
    url: "/api/documents/d1/versions/3",
    reply: {
      ...fakeVersion({ number: 3 }),
      content: {},
      corrections: [],
      unprintable: [],
    },
  },
  {
    name: "restoreVersion",
    run: () => api.restoreVersion("d1", 3),
    method: "POST",
    url: "/api/documents/d1/versions/3/restore",
    reply: document,
  },
  {
    name: "approveVersion",
    run: () => api.approveVersion("d1", 3),
    method: "POST",
    url: "/api/documents/d1/versions/3/approve",
    reply: fakeVersion(),
  },
  {
    name: "markSent",
    run: () => api.markSent("d1", 3),
    method: "POST",
    url: "/api/documents/d1/versions/3/mark-sent",
    reply: fakeVersion(),
  },
  {
    name: "listMessages",
    run: () => api.listMessages("d1", "design"),
    method: "GET",
    url: "/api/documents/d1/messages?channel=design",
    reply: { messages: [message] },
  },
  {
    name: "sendMessage",
    run: () => api.sendMessage("d1", "Shorter"),
    method: "POST",
    url: "/api/documents/d1/messages",
    body: { text: "Shorter", channel: "content" },
    reply: { message, generation },
    status: 202,
  },
  {
    name: "getGeneration",
    run: () => api.getGeneration("g1"),
    method: "GET",
    url: "/api/document-generations/g1",
    reply: detail,
  },
  {
    name: "cancelGeneration",
    run: () => api.cancelGeneration("g1"),
    method: "POST",
    url: "/api/document-generations/g1/cancel",
    reply: generation,
  },
  {
    name: "listTemplates",
    run: () => api.listTemplates(),
    method: "GET",
    url: "/api/document-templates",
    reply: { templates: [template] },
  },
  {
    name: "deleteTemplate",
    run: () => api.deleteTemplate("upload-ab12cd34"),
    method: "DELETE",
    url: "/api/document-templates/upload-ab12cd34",
    status: 204,
  },
  {
    name: "getConsent",
    run: () => api.getConsent(),
    method: "GET",
    url: "/api/documents/consent",
    reply: fakeConsent(),
  },
  {
    name: "acceptConsent",
    run: () => api.acceptConsent(1),
    method: "POST",
    url: "/api/documents/consent",
    body: { version: 1 },
    reply: fakeConsent({ accepted: true }),
  },
  {
    name: "getSpend",
    run: () => api.getSpend(55),
    method: "GET",
    url: "/api/documents/spend?min_fit=55",
    reply: spend,
  },
  {
    name: "getSettings",
    run: () => api.getSettings(),
    method: "GET",
    url: "/api/documents/settings",
    reply: { no_dash_separators: false, banned_phrases: [] },
  },
  {
    name: "putSettings",
    run: () =>
      api.putSettings({
        no_dash_separators: true,
        banned_phrases: ["synergy"],
      }),
    method: "PUT",
    url: "/api/documents/settings",
    body: { no_dash_separators: true, banned_phrases: ["synergy"] },
    reply: { no_dash_separators: true, banned_phrases: ["synergy"] },
  },
];

describe("documents-api", () => {
  it.each(CASES)("$name sends $method $url", async (one) => {
    let seen:
      | { method: string; url: string; body: unknown; headers: Headers }
      | undefined;
    server.use(
      http.all("*/api/*", async ({ request }) => {
        const text = await request.clone().text();
        const url = new URL(request.url);
        seen = {
          method: request.method,
          url: `${url.pathname}${url.search}`,
          body: text === "" ? undefined : (JSON.parse(text) as unknown),
          headers: request.headers,
        };
        return one.status === 204
          ? new HttpResponse(null, { status: 204 })
          : HttpResponse.json((one.reply ?? null) as JsonBodyType, {
              status: one.status ?? 200,
            });
      }),
    );
    await one.run();
    expect(seen?.method).toBe(one.method);
    expect(seen?.url).toBe(one.url);
    expect(seen?.body).toEqual(one.body);
    for (const [name, value] of Object.entries(one.headers ?? {}))
      expect(seen?.headers.get(name)).toBe(value);
    // The CSRF header of the app's rule on every write, and on no read.
    expect(seen?.headers.get("x-requested-with")).toBe(
      one.method === "GET" ? null : "jobapp",
    );
  });

  it("covers every function of the module that makes a call", () => {
    const covered = new Set(CASES.map((one) => one.name));
    // Addresses (`...Url`), query keys (`...Key`) and the two readers make no call.
    const reads = ["staleDraft", "failureSentence"];
    const calls = Object.entries(api)
      .filter(([, value]) => typeof value === "function")
      .map(([name]) => name)
      .filter((name) => !/(Url|Key)$/.test(name) && !reads.includes(name));
    expect(calls.filter((name) => !covered.has(name))).toEqual([]);
  });

  it("encodes an id into its path and never lets it climb", async () => {
    let path = "";
    server.use(
      http.all("*/api/*", ({ request }) => {
        path = new URL(request.url).pathname;
        return HttpResponse.json(document);
      }),
    );
    await api.getDocument("a/../b");
    expect(path).toBe("/api/documents/a%2F..%2Fb");
  });

  it("builds the addresses the browser fetches itself", () => {
    expect(api.versionPdfUrl("d1", 2)).toBe("/api/documents/d1/versions/2/pdf");
    expect(api.versionPage1Url("d1", 2)).toBe(
      "/api/documents/d1/versions/2/page1.png",
    );
    expect(api.versionPage1Url("d1", 2, 900)).toBe(
      "/api/documents/d1/versions/2/page1.png?width=900",
    );
    expect(api.originalPageUrl("d1", 2)).toBe(
      "/api/documents/d1/original?page=2",
    );
    expect(api.templateThumbnailUrl("classic", "letter")).toBe(
      "/api/document-templates/classic/thumbnail?kind=letter",
    );
  });

  it("reads the current document out of the 409 of a stale save", async () => {
    server.use(
      http.put("*/api/documents/d1/draft", () =>
        HttpResponse.json(
          {
            detail: "Changed elsewhere.",
            document: { ...document, draft_revision: 5 },
          },
          { status: 409 },
        ),
      ),
    );
    const error: unknown = await api
      .saveDraft("d1", {}, 4)
      .catch((e: unknown) => e);
    expect(api.staleDraft(error)?.document.draft_revision).toBe(5);
    expect(
      api.staleDraft(new ApiError("http", "x", { status: 500, body: {} })),
    ).toBeUndefined();
    expect(
      api.staleDraft(
        new ApiError("http", "x", { status: 409, body: { detail: "x" } }),
      ),
    ).toBeUndefined();
    expect(api.staleDraft(new Error("no"))).toBeUndefined();
  });

  it("rejects an answer of another shape as a parse error", async () => {
    server.use(
      http.get("*/api/documents/consent", () =>
        HttpResponse.json({ text: CONSENT_TEXT }),
      ),
    );
    await expect(api.getConsent()).rejects.toThrow();
  });
});

describe("the separate capture read contract", () => {
  it("defaults an older response to idle without changing content generation", () => {
    const older = { ...fakeCv() };
    Reflect.deleteProperty(older, "design_generation");
    const decoded = api.baseCvSchema.parse(older);
    expect(decoded).toHaveProperty("design_generation", null);
    expect(decoded.generation).toEqual(older.generation);
  });

  it.each(api.GENERATION_STATUSES)(
    "accepts the capture status %s independently",
    (status) => {
      const capture = { ...generation, status };
      expect(
        api.baseCvSchema.parse({ ...cv, design_generation: capture }),
      ).toHaveProperty("design_generation", capture);
    },
  );

  it.each([
    {},
    { ...generation, status: "unknown" },
    { ...generation, wait_reason: "private" },
    "queued",
  ])("refuses a malformed present capture %j", (capture) => {
    expect(
      api.baseCvSchema.safeParse({ ...cv, design_generation: capture }).success,
    ).toBe(false);
  });
});
