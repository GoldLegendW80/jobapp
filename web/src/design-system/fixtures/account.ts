import { delay, http, HttpResponse } from "msw";

import type { ModelCredential } from "@/features/agents/api/agents-api";
import type { MailAccount } from "@/features/mail/api/account-api";
import { server } from "@/test/server";

/** Invented settings of the Account page stories: a person who connected everything. */

export const SAVED_CREDENTIAL: ModelCredential = {
  kind: "claude_subscription",
  last4: "Tk7q",
  verified_at: "2026-11-02T08:05:00Z",
  updated_at: "2026-11-02T08:00:00Z",
};

export const CONNECTED_GMAIL: MailAccount = {
  available: true,
  status: "ok",
  email: "e2e-zoe@example.org",
  can_read: true,
  can_send: true,
  connected_at: "2026-10-14T09:00:00Z",
  last_sync_at: "2026-11-05T07:45:00Z",
  consent: {
    granted: true,
    at: "2026-10-14T09:00:00Z",
    version: 1,
    current_version: 1,
  },
};

const SIGNED_IN_SITES = {
  sites: [
    {
      host: "jobs.lumenfreight.example.org",
      state: "signed_in",
      signed_in_at: "2026-11-01T10:12:00Z",
    },
    {
      host: "careers.kestrelhealth.example.org",
      state: "expired",
      signed_in_at: "2026-10-12T16:40:00Z",
    },
  ],
};

const FEEDS_WITH_A_KEY = {
  feeds: [
    { name: "arbeitnow", needs_key: false, key_stored: false, key_last4: null },
    { name: "himalayas", needs_key: false, key_stored: false, key_last4: null },
    {
      name: "adzuna",
      needs_key: true,
      key_stored: true,
      key_last4: "9f2c",
      key_saved_at: "2026-10-20T18:30:00Z",
    },
  ],
  keys_available: true,
};

/** Everything the Account page reads, answered as for a person who connected it all. */
export function fakeConnectedAccount() {
  server.use(
    http.get("*/api/me/model-credential", () =>
      HttpResponse.json(SAVED_CREDENTIAL),
    ),
    http.get("*/api/mail/account", () => HttpResponse.json(CONNECTED_GMAIL)),
    http.get("*/api/browser/sites", () => HttpResponse.json(SIGNED_IN_SITES)),
    http.get("*/api/jobs/feeds", () => HttpResponse.json(FEEDS_WITH_A_KEY)),
  );
}

const ACCOUNT_READS = [
  "*/api/me/model-credential",
  "*/api/mail/account",
  "*/api/browser/sites",
  "*/api/jobs/feeds",
];

/** Every card's read fails with a server error, or never answers. */
export function failAccountReads(answer: "error" | "loading") {
  server.use(
    ...ACCOUNT_READS.map((path) =>
      http.get(path, async () => {
        if (answer === "loading") await delay("infinite");
        return HttpResponse.json(
          { detail: "Something went wrong on the server." },
          { status: 500 },
        );
      }),
    ),
  );
}
