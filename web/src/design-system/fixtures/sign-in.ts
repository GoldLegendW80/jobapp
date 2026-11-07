import { delay, http, HttpResponse } from "msw";

import type { Invite, User } from "@/features/auth/api/auth-api";
import { fakeSession } from "@/test/auth";
import { server } from "@/test/server";

/** A link token of the api's shape (43 URL-safe characters), invented. */
export const LINK_TOKEN = "storybookLinkToken_0123456789".padEnd(43, "x");

/** The person a link was made for: an invented address. */
export const LINK_EMAIL = "new.friend@example.org";

export const inviteLink: Invite = {
  email: LINK_EMAIL,
  kind: "invite",
  expires_at: "2026-11-13T10:00:00Z",
};

export const resetLink: Invite = {
  email: LINK_EMAIL,
  kind: "reset",
  expires_at: "2026-11-06T11:00:00Z",
};

export const signedInFriend: User = {
  id: "00000000-0000-4000-8000-000000000042",
  email: LINK_EMAIL,
  name: "Inès Marlowe",
  role: "member",
  created_at: "2026-11-06T09:00:00Z",
  last_login_at: "2026-11-06T09:00:00Z",
};

/** What GET /api/auth/invites/:token answers: the link, a dead link, an error, or nothing yet. */
export type LinkAnswer = Invite | "dead" | "error" | "pending";

/** Nobody signed in, and the link of the page answers `answer`; accepting it signs the person in. */
export function fakeLink(answer: LinkAnswer) {
  const session = fakeSession(null);
  server.use(
    http.get("*/api/auth/invites/:token", async () => {
      if (answer === "pending") await delay("infinite");
      if (answer === "dead")
        return HttpResponse.json(
          {
            detail:
              "This link is not valid. It may have expired or been used already.",
          },
          { status: 404 },
        );
      if (answer === "error")
        return HttpResponse.json(
          { detail: "The server had a problem. Try again in a moment." },
          { status: 500 },
        );
      return HttpResponse.json(answer);
    }),
    http.post("*/api/auth/invites/:token/accept", () => {
      session.user = signedInFriend;
      return HttpResponse.json(signedInFriend);
    }),
  );
}

/** Nobody signed in, and POST /api/auth/login answers `respond`. */
export function fakeLogin(respond: () => Response | Promise<Response>) {
  fakeSession(null);
  server.use(http.post("*/api/auth/login", respond));
}
