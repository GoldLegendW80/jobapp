/**
 * A fake of the deck's api for the tests of this folder: the deck in pages of `pageSize`, the
 * Pass and its undo, apply's Like and cancel, and the application read before a cancel. Every
 * call is recorded in order (`calls`), so a test can assert what was sent and what never was.
 */
import { http, HttpResponse, type JsonBodyType } from "msw";

import { fakeApplication } from "@/test/apply";
import { server } from "@/test/server";

import type { Card, Mode } from "../opportunities-api";

export function makeCard(n: number, overrides: Partial<Card> = {}): Card {
  return {
    id: `00000000-0000-4000-9000-${String(n).padStart(12, "0")}`,
    title: `Example Role ${n}`,
    company: `Example Robotics ${n}`,
    location: "Lyon, France",
    contract_type: "CDI",
    source: "greenhouse:example",
    url: `https://boards.example.org/example/jobs/${n}`,
    fit: 90 - n,
    found_on: "2026-10-18",
    reason: {
      matches: ["Python", "FastAPI"],
      gap: "Kubernetes",
      text: "Matches: Python, FastAPI. Gap: Kubernetes.",
    },
    pay: null,
    work_mode: null,
    ...overrides,
  };
}

export interface FakeDeck {
  calls: string[];
  /** Like answers by posting id: a status and body, instead of the 201. */
  likeAnswers: Map<string, { status: number; body: JsonBodyType }>;
  passAnswers: Map<string, { status: number; body: JsonBodyType }>;
  /** The state GET /api/applications/{id} answers before a cancel. */
  applicationState: string;
  /** While set, the Like waits for this promise before it answers. */
  likeGate: Promise<void> | null;
}

export function fakeDeck(
  cards: Card[],
  {
    mode = "review",
    minFit = 70,
    pageSize = 20,
  }: { mode?: Mode; minFit?: number; pageSize?: number } = {},
): FakeDeck {
  const state: FakeDeck = {
    calls: [],
    likeAnswers: new Map(),
    passAnswers: new Map(),
    applicationState: "queued",
    likeGate: null,
  };
  const passed = new Set<string>();
  server.use(
    http.get("*/api/postings/deck", ({ request }) => {
      const url = new URL(request.url);
      state.calls.push(`GET deck ${url.search}`);
      const shown = cards.filter((card) => !passed.has(card.id));
      const from = Number(url.searchParams.get("cursor") ?? "0");
      const page = shown.slice(from, from + pageSize);
      const next =
        from + pageSize < shown.length ? String(from + pageSize) : null;
      return HttpResponse.json({
        cards: page,
        next_cursor: next,
        left: shown.length - from,
        mode,
        min_fit: minFit,
        rules_unreadable: false,
      });
    }),
    http.post("*/api/postings/:id/pass", ({ params }) => {
      const id = String(params.id);
      state.calls.push(`POST pass ${id}`);
      const answer = state.passAnswers.get(id);
      if (answer)
        return HttpResponse.json(answer.body, { status: answer.status });
      passed.add(id);
      return HttpResponse.json({ id, status: "skipped", changed: true });
    }),
    http.delete("*/api/postings/:id/pass", ({ params }) => {
      const id = String(params.id);
      state.calls.push(`DELETE pass ${id}`);
      passed.delete(id);
      return HttpResponse.json({ id, status: "found", changed: true });
    }),
    http.post("*/api/postings/:id/like", async ({ params }) => {
      const id = String(params.id);
      state.calls.push(`POST like ${id}`);
      if (state.likeGate) await state.likeGate;
      const answer = state.likeAnswers.get(id);
      if (answer)
        return HttpResponse.json(answer.body, { status: answer.status });
      return HttpResponse.json(
        {
          application: fakeApplication("queued", { posting_id: id }),
          documents: ["cv", "letter"],
        },
        { status: 201 },
      );
    }),
    http.get("*/api/applications/:id", ({ params }) => {
      state.calls.push(`GET application ${String(params.id)}`);
      return HttpResponse.json({
        ...fakeApplication(state.applicationState),
        events: [],
      });
    }),
    http.post("*/api/applications/:id/cancel", ({ params }) => {
      state.calls.push(`POST cancel ${String(params.id)}`);
      return HttpResponse.json(fakeApplication("cancelled"));
    }),
  );
  return state;
}

export const refused = (code: string, detail: string) => ({
  status: 409,
  body: { detail, code, application_id: null },
});
