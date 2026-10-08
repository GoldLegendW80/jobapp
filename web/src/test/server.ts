import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";

/** Default handlers: a healthy api. Tests override per case with `server.use(...)`. */
export const handlers = [
  http.get("*/api/health", () => HttpResponse.json({ status: "ok" })),
];

export const server = setupServer(...handlers);
