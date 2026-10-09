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
  http.get("*/api/auth/me", () =>
    HttpResponse.json({ detail: "Not signed in." }, { status: 401 }),
  ),
];

export const server = setupServer(...handlers);
