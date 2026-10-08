import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";

/** Default handlers: a healthy api and nobody signed in. Tests override per case with `server.use(...)`. */
export const handlers = [
  http.get("*/api/health", () => HttpResponse.json({ status: "ok" })),
  http.get("*/api/auth/me", () =>
    HttpResponse.json({ detail: "Not signed in." }, { status: 401 }),
  ),
];

export const server = setupServer(...handlers);
