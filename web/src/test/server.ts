import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";

/** Default handlers: a healthy api, nobody signed in, and no jobs. Tests override per case with `server.use(...)`. */
/** An empty jobs list, the home page's first request once signed in. */
export const emptyJobsPage = {
  items: [],
  next_cursor: null,
  total: 0,
  counts_by_status: {},
  fit_histogram: {
    buckets: Array.from({ length: 10 }, (_, i) => ({
      low: i * 10,
      high: i === 9 ? 100 : i * 10 + 9,
      count: 0,
    })),
    unscored: 0,
  },
};

/** No sweep yet, the automatic search off: what GET /api/jobs/sweeps answers a new account. */
export const emptySweeps = {
  items: [],
  schedule: {
    enabled: false,
    days: [
      "monday",
      "tuesday",
      "wednesday",
      "thursday",
      "friday",
      "saturday",
      "sunday",
    ],
    times: ["06:17"],
    timezone: "Europe/Paris",
    active: false,
    next_run: null,
  },
};

export const handlers = [
  http.get("*/api/jobs", () => HttpResponse.json(emptyJobsPage)),
  http.get("*/api/jobs/sweeps", () => HttpResponse.json(emptySweeps)),
  http.get("*/api/health", () => HttpResponse.json({ status: "ok" })),
  // No model credential saved (the Account page's Model access card).
  http.get("*/api/me/model-credential", () =>
    HttpResponse.json(
      { detail: "No model credential is saved." },
      { status: 404 },
    ),
  ),
  // No Gmail connected (the Account page's Gmail card).
  http.get("*/api/mail/account", () =>
    HttpResponse.json({
      available: true,
      status: "not_connected",
      email: null,
      can_read: false,
      can_send: false,
      connected_at: null,
      last_sync_at: null,
      consent: { granted: false, at: null, version: null, current_version: 1 },
    }),
  ),
  // The browser free, no session of the person (the Account page's Your browser card).
  http.get("*/api/browser", () =>
    HttpResponse.json({
      available: true,
      profile: { exists: false, size_bytes: 0, last_used_at: null },
      lease: null,
      holder: null,
      queue: [],
    }),
  ),
  // No agent run and no proposal yet (job detail's Agent assessment section).
  http.get("*/api/agents/runs", () => HttpResponse.json({ items: [] })),
  http.get("*/api/proposals", () => HttpResponse.json({ items: [] })),
  // No chat thread yet (job detail's Chat panel).
  http.get("*/api/agents/threads", () => HttpResponse.json({ items: [] })),
  // Nothing written for a posting, one base CV, the default threshold (job detail's Documents section).
  http.get("*/api/jobs/:id/documents", () =>
    HttpResponse.json({ documents: [] }),
  ),
  http.get("*/api/documents/base-cvs", () =>
    HttpResponse.json({
      base_cvs: [
        {
          id: "cv-default",
          name: "My CV",
          source: "profile",
          template_slug: "classic",
          language: "en",
          is_default: true,
          created_at: "2026-10-14T08:00:00Z",
          updated_at: "2026-10-14T09:00:00Z",
          last_version_at: "2026-10-14T09:00:00Z",
          render_state: "idle",
          generation: null,
        },
      ],
      max: 10,
    }),
  ),
  http.get("*/api/documents/spend", () =>
    HttpResponse.json({
      day: "2026-10-14",
      auto: true,
      min_fit: 70,
      daily_ceiling: 100,
      written_today: 0,
      waiting: 0,
      postings_30_days: 0,
      to_write: 0,
      to_write_estimate: { tokens: { low: 0, high: 0 }, cost: null },
      newly: 0,
      newly_estimate: { tokens: { low: 0, high: 0 }, cost: null },
      priced: false,
      cost_note: "",
      today: { input: 0, output: 0, cost_usd: "0" },
      last_30_days: { input: 0, output: 0, cost_usd: "0" },
    }),
  ),
  // No writing rule yet, the default of a new user (the Search tab's Writing rules card).
  http.get("*/api/documents/settings", () =>
    HttpResponse.json({ no_dash_separators: false, banned_phrases: [] }),
  ),
  // No application started for a posting (job detail's Apply section).
  http.get("*/api/applications", () => HttpResponse.json({ items: [] })),
  // Nothing waits in the review queue (the app shell's Applications count).
  http.get("*/api/apply/queue/count", () =>
    HttpResponse.json({ waiting_on_you: 0 }),
  ),
  http.get("*/api/auth/me", () =>
    HttpResponse.json({ detail: "Not signed in." }, { status: 401 }),
  ),
];

export const server = setupServer(...handlers);
