/**
 * A fake of /api/insights for tests and stories: the numbers it is given, the close of the
 * silent applications (the ids not in `stillSilent` come back skipped) and the bulk undo. Every
 * call is recorded in order (`calls`) with what it sent.
 */
import { http, HttpResponse } from "msw";

import type { Insights } from "@/features/jobs/api/insights-api";
import { server } from "@/test/server";

export interface FakeInsights {
  calls: { path: string; body?: unknown }[];
  /** The ids the close finds still silent; null: every id it is sent. */
  stillSilent: Set<string> | null;
  /** While set, GET /api/insights answers this status with an error. */
  failWith: number | null;
}

export function fakeInsights(data: Insights): FakeInsights {
  const state: FakeInsights = { calls: [], stillSilent: null, failWith: null };
  let current = data;
  server.use(
    http.get("*/api/insights", () => {
      state.calls.push({ path: "GET insights" });
      if (state.failWith)
        return HttpResponse.json(
          { detail: "The server had a problem." },
          { status: state.failWith },
        );
      return HttpResponse.json(current);
    }),
    http.post("*/api/insights/silent/close", async ({ request }) => {
      const body = (await request.json()) as { ids: string[] };
      state.calls.push({ path: "POST close", body });
      const closed = body.ids.filter(
        (id) => state.stillSilent === null || state.stillSilent.has(id),
      );
      current = {
        ...current,
        silent: current.silent.filter((row) => !closed.includes(row.id)),
      };
      return HttpResponse.json({
        closed,
        skipped: body.ids.filter((id) => !closed.includes(id)),
        undo_token: closed.length ? "undo-token" : null,
        undo_expires_at: closed.length ? "2026-11-07T10:10:00Z" : null,
      });
    }),
    http.post("*/api/jobs/bulk/undo", async ({ request }) => {
      const body = (await request.json()) as { token: string };
      state.calls.push({ path: "POST undo", body });
      current = data;
      return HttpResponse.json({
        restored: data.silent.map((row) => row.id),
        skipped: [],
      });
    }),
  );
  return state;
}
