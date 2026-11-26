import { http, HttpResponse } from "msw";

import type { Criteria, SavedSearch } from "@/features/jobs/api/searches-api";
import type { Sweep } from "@/features/jobs/api/sweeps-api";

import { emptySweeps } from "./handlers";
import { server } from "./server";

/** A saved search as GET /api/jobs/searches lists it. */
export function makeSearch(
  id: string,
  name: string,
  criteria: Criteria = {},
  overrides: Partial<SavedSearch> = {},
): SavedSearch {
  return {
    id,
    name,
    main: id === "main",
    revision: 1,
    criteria,
    schedule: { ...emptySweeps.schedule },
    request: null,
    ...overrides,
  };
}

/**
 * The searches of /api/jobs/searches (jobs J32), as the api answers them: the main one first
 * (its criteria read live from `main`), names unique case aside and never "Main search" (409), a
 * stale revision 409 with the stored one, a ticked platform the person gave no permission for
 * 422 (`permitted`), one waiting run per search while `running` holds a sweep's id.
 */
export function fakeSearches({
  main = () => ({}),
  saved = [],
  permitted = () => true,
  started,
}: {
  main?: () => Criteria;
  saved?: SavedSearch[];
  permitted?: (criteria: Criteria) => boolean;
  /** The sweep a run starts, when none runs. */
  started?: (id: string, name: string) => Sweep;
} = {}) {
  const state = {
    saved: structuredClone(saved),
    line: [] as string[],
    running: null as string | null,
    writes: [] as { method: string; id: string; body: unknown }[],
    runs: [] as string[],
  };
  const listed = () => [
    makeSearch("main", "Main search", main()),
    ...state.saved,
  ];
  const withLine = (search: SavedSearch): SavedSearch => {
    const place = state.line.indexOf(search.id);
    return place < 0
      ? { ...search, request: null }
      : {
          ...search,
          request: {
            id: `request-${search.id}`,
            search_id: search.id,
            place: place + 1,
            trigger: "manual",
            requested_at: "2026-11-26T09:00:00Z",
          },
        };
  };
  const taken = (name: string, except: string) =>
    name.toLowerCase() === "main search" ||
    state.saved.some(
      (one) =>
        one.id !== except && one.name.toLowerCase() === name.toLowerCase(),
    );
  const run = (id: string) => {
    const search = listed().find((one) => one.id === id);
    if (!search) return null;
    state.runs.push(id);
    if (state.running || !started) {
      if (!state.line.includes(id)) state.line.push(id);
      return {
        search_id: id,
        outcome: "queued",
        sweep: null,
        request: withLine(search).request,
      };
    }
    const sweep = started(id, search.name);
    state.running = sweep.id;
    return { search_id: id, outcome: "started", sweep, request: null };
  };
  server.use(
    http.get("*/api/jobs/searches", () =>
      HttpResponse.json({
        items: listed().map(withLine),
        running_sweep_id: state.running,
      }),
    ),
    http.post("*/api/jobs/searches/run", () =>
      HttpResponse.json(
        { items: listed().map((one) => run(one.id)) },
        { status: 202 },
      ),
    ),
    http.post("*/api/jobs/searches/:id/run", ({ params }) => {
      const ran = run(String(params.id));
      return ran
        ? HttpResponse.json(ran, { status: 202 })
        : HttpResponse.json({ detail: "Search not found." }, { status: 404 });
    }),
    http.delete("*/api/jobs/searches/:id/request", ({ params }) => {
      const before = state.line.length;
      state.line = state.line.filter((one) => one !== String(params.id));
      return before === state.line.length
        ? HttpResponse.json(
            { detail: "No run of this search is waiting." },
            { status: 404 },
          )
        : new HttpResponse(null, { status: 204 });
    }),
    http.post("*/api/jobs/searches", async ({ request }) => {
      const body = (await request.json()) as {
        name: string;
        criteria: Criteria;
        schedule: SavedSearch["schedule"];
      };
      state.writes.push({ method: "POST", id: "", body });
      if (taken(body.name, ""))
        return HttpResponse.json(
          { detail: "You already have a search with this name." },
          { status: 409 },
        );
      if (!permitted(body.criteria))
        return HttpResponse.json(
          {
            detail:
              "Give your permission to search www.indeed.com in Find jobs first.",
          },
          { status: 422 },
        );
      const made = makeSearch(
        `00000000-0000-4000-8000-${String(state.saved.length + 1).padStart(12, "0")}`,
        body.name,
        body.criteria,
        { schedule: { ...emptySweeps.schedule, ...body.schedule } },
      );
      state.saved.push(made);
      return HttpResponse.json(made, { status: 201 });
    }),
    http.put("*/api/jobs/searches/:id", async ({ params, request }) => {
      const id = String(params.id);
      const body = (await request.json()) as {
        name: string;
        criteria: Criteria;
        revision: number;
      };
      state.writes.push({ method: "PUT", id, body });
      const found = state.saved.find((one) => one.id === id);
      if (!found)
        return HttpResponse.json(
          { detail: "Search not found." },
          { status: 404 },
        );
      if (found.revision !== body.revision)
        return HttpResponse.json(
          {
            detail: "This search changed in another tab. Open it again.",
            revision: found.revision,
          },
          { status: 409 },
        );
      if (taken(body.name, id))
        return HttpResponse.json(
          { detail: "You already have a search with this name." },
          { status: 409 },
        );
      if (!permitted(body.criteria))
        return HttpResponse.json(
          {
            detail:
              "Give your permission to search www.indeed.com in Find jobs first.",
          },
          { status: 422 },
        );
      Object.assign(found, {
        name: body.name,
        criteria: body.criteria,
        revision: found.revision + 1,
      });
      return HttpResponse.json(found);
    }),
    http.delete("*/api/jobs/searches/:id", ({ params }) => {
      state.writes.push({
        method: "DELETE",
        id: String(params.id),
        body: null,
      });
      state.saved = state.saved.filter((one) => one.id !== String(params.id));
      state.line = state.line.filter((one) => one !== String(params.id));
      return new HttpResponse(null, { status: 204 });
    }),
  );
  return state;
}
