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
  http.get("*/api/auth/me", () =>
    HttpResponse.json({ detail: "Not signed in." }, { status: 401 }),
  ),
];

export const server = setupServer(...handlers);
