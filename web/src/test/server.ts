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

export const handlers = [
  http.get("*/api/jobs", () => HttpResponse.json(emptyJobsPage)),
  http.get("*/api/health", () => HttpResponse.json({ status: "ok" })),
  http.get("*/api/auth/me", () =>
    HttpResponse.json({ detail: "Not signed in." }, { status: 401 }),
  ),
];

export const server = setupServer(...handlers);
