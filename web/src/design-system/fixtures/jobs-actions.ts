import { delay, http, HttpResponse } from "msw";

import type { ScheduleState } from "@/features/jobs/api/sweeps-api";
import type { Search } from "@/features/profile/api/profile-api";
import { makeDetail } from "@/test/jobs";
import { emptySweeps, server } from "@/test/server";

import { camilleProfile } from "./camille-profile";
import { POSTINGS } from "./postings";

const serverError = () =>
  HttpResponse.json({ detail: "Server error" }, { status: 500 });

/** POST /api/jobs/bulk sets the status or appends the note, with an undo; `fail` answers a 500. */
export function fakeBulkUpdate({ fail = false }: { fail?: boolean } = {}) {
  server.use(
    http.post("*/api/jobs/bulk", async ({ request }) => {
      if (fail) return serverError();
      const { ids } = (await request.json()) as { ids: string[] };
      return HttpResponse.json({
        results: ids.map((id) => ({
          id,
          changed: true,
          status: "shortlisted",
          previous_status: "found",
        })),
        changed: ids.length,
        unchanged: 0,
        undo_token: "undo-0001",
        undo_expires_at: "2026-11-06T10:10:00Z",
      });
    }),
    http.post("*/api/jobs/bulk/undo", () =>
      HttpResponse.json({ restored: ["a", "b", "c"], skipped: [] }),
    ),
  );
}

/** POST /api/jobs: the posting added and scored, already in the list, refused, or still sending. */
export function fakeAddPosting(
  outcome: "added" | "duplicate" | "error" | "pending" = "added",
) {
  server.use(
    http.post("*/api/jobs", async ({ request }) => {
      if (outcome === "pending") await delay("infinite");
      if (outcome === "error") return serverError();
      const existing = POSTINGS[0];
      if (outcome === "duplicate" && existing)
        return HttpResponse.json(
          {
            detail: "This posting is already in your list.",
            existing_id: existing.id,
            existing_slug: existing.slug,
          },
          { status: 409 },
        );
      const body = (await request.json()) as Record<string, string>;
      return HttpResponse.json(
        makeDetail({
          id: "00000000-0000-4000-9000-000000000201",
          company: body.company ?? "",
          title: body.title ?? "",
          fit: 74,
        }),
      );
    }),
  );
}

/** POST /api/jobs/rescore: done in one call, stuck after its first 40, refused, or failing. */
export function fakeRescore(
  outcome: "done" | "running" | "refused" | "error" = "done",
) {
  let calls = 0;
  server.use(
    http.post("*/api/jobs/rescore", async () => {
      calls += 1;
      if (outcome === "error") return serverError();
      if (outcome === "refused")
        return HttpResponse.json(
          {
            detail:
              "Your profile has no skills to score with yet. Add them in Profile, then rescore.",
          },
          { status: 409 },
        );
      if (outcome === "running" && calls > 1) await delay("infinite");
      const running = outcome === "running";
      return HttpResponse.json({
        scored: running ? 40 : 240,
        changed: running ? 12 : 63,
        unchanged: running ? 28 : 165,
        skipped_manual: 2,
        skipped_no_text: running ? 0 : 10,
        skipped_busy: 0,
        remaining: running ? 200 : 0,
        next_after: running ? "00000000-0000-4000-9000-000000000040" : null,
      });
    }),
  );
}

/** The automatic search on every weekday at 07:30 in Paris, with a ticked website. */
export const WEEKDAYS_SCHEDULE: ScheduleState = {
  enabled: true,
  days: ["monday", "tuesday", "wednesday", "thursday", "friday"],
  times: ["07:30", "18:00"],
  timezone: "Europe/Paris",
  active: true,
  next_run: "2026-11-09T06:30:00Z",
};

/** GET /api/jobs/sweeps with no past search and `schedule`. */
export function fakeSchedule(schedule: ScheduleState = emptySweeps.schedule) {
  server.use(
    http.get("*/api/jobs/sweeps", () =>
      HttpResponse.json({ items: [], schedule }),
    ),
  );
}

/**
 * GET /api/jobs/feeds and Adzuna's key: none stored (`stored` gives one), saved and removed as
 * the api does. `feeds` answers the listing's failure or a server that cannot store keys.
 */
export function fakeAdzunaKey({
  stored = false,
  feeds = "ok",
}: {
  stored?: boolean;
  feeds?: "ok" | "error" | "pending" | "no_keys";
} = {}) {
  let key = stored
    ? {
        key_stored: true,
        key_last4: "7f3a",
        key_saved_at: "2026-10-28T19:04:00Z",
      }
    : { key_stored: false, key_last4: null, key_saved_at: null };
  const listing = () => ({
    feeds: [
      { name: "arbeitnow", needs_key: false },
      { name: "himalayas", needs_key: false },
      { name: "adzuna", needs_key: true, ...key },
    ],
    keys_available: feeds !== "no_keys",
  });
  server.use(
    http.get("*/api/jobs/feeds", async () => {
      if (feeds === "pending") await delay("infinite");
      if (feeds === "error") return serverError();
      return HttpResponse.json(listing());
    }),
    http.put("*/api/jobs/feeds/adzuna/key", async ({ request }) => {
      const body = (await request.json()) as { app_key: string };
      key = {
        key_stored: true,
        key_last4: body.app_key.slice(-4),
        key_saved_at: "2026-11-06T09:30:00Z",
      };
      return HttpResponse.json({ name: "adzuna", needs_key: true, ...key });
    }),
    http.delete("*/api/jobs/feeds/adzuna/key", () => {
      key = { key_stored: false, key_last4: null, key_saved_at: null };
      return new HttpResponse(null, { status: 204 });
    }),
  );
}

/** Camille's search with the places Find jobs lists: two websites and two job feeds. */
export function findJobsSearch(): Search {
  const search = camilleProfile().search;
  return {
    ...search,
    interactive_sites: [
      {
        platform: "welcome_to_the_jungle",
        url: "https://www.welcometothejungle.com/",
        enabled: true,
        mode: "llm_browser",
      },
      {
        platform: "custom",
        url: "https://jobs.example.org/",
        enabled: true,
        mode: "llm_browser",
      },
    ],
    job_feeds: ["arbeitnow", "himalayas"],
  };
}

/** Each known job website's permission, as the api answers it: shown with its risk sentence. */
export function fakeSearchPermissions({ given = false } = {}) {
  const permission = {
    enabled: given,
    accepted: given,
    allowed: given,
    text: "The website may limit or close an account it sees searched by a program. The app searches it in your own browser, slowly, and only while you allow it.",
    text_version: 1,
    query: null,
    paused_until: null,
  };
  server.use(
    http.get("*/api/browser/linkedin", () => HttpResponse.json(permission)),
    http.get("*/api/opportunities/platforms/:kind", () =>
      HttpResponse.json(permission),
    ),
  );
}

/** POST /api/jobs/sweeps: the search does not start (a 500). */
export function failSweepStart() {
  server.use(http.post("*/api/jobs/sweeps", serverError));
}
