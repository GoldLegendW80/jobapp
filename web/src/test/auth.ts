import { http, HttpResponse } from "msw";

import type { User } from "@/features/auth/api";

import { server } from "./server";

export const admin: User = {
  id: "00000000-0000-4000-8000-000000000001",
  email: "harald@example.org",
  name: "Harald Example",
  role: "admin",
  created_at: "2026-10-08T10:00:00Z",
  last_login_at: null,
};

export const member: User = {
  id: "00000000-0000-4000-8000-000000000002",
  email: "friend@example.org",
  name: "Zoë Ångström",
  role: "member",
  created_at: "2026-10-08T10:00:00Z",
  last_login_at: "2026-10-08T11:00:00Z",
};

export const unauthorized = () =>
  HttpResponse.json({ detail: "Not signed in." }, { status: 401 });

/**
 * A fake api session: /me answers the signed in user or 401, login and accept sign in, logout
 * signs out. Returns the state, so a test can change it (session ended elsewhere) or inspect it.
 */
export function fakeSession(initial: User | null = null) {
  const state = { user: initial, logouts: 0 };
  server.use(
    http.get("*/api/auth/me", () =>
      state.user ? HttpResponse.json(state.user) : unauthorized(),
    ),
    http.post("*/api/auth/logout", () => {
      state.user = null;
      state.logouts += 1;
      return new HttpResponse(null, { status: 204 });
    }),
  );
  return state;
}
