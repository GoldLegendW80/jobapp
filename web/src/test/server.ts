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

/** GET /api/apply/settings of a new account: Review first after a Like, the review box unticked. */
export const defaultApplySettings = {
  mode_after_like: "review",
  full_auto_available: false,
  paused: false,
  min_fit: 80,
  min_fit_chosen: false,
  safety_ceiling: 30,
  hold_s: 900,
  hold_min_s: 900,
  company_days: 30,
  show_postings_from: null,
  write_documents_from: null,
  rehearsals: 3,
  rehearsals_done: 0,
  full_auto_since: null,
  review_before_send: false,
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

export const noFeedKeys = {
  feeds: [
    { name: "arbeitnow", needs_key: false, key_stored: false, key_last4: null },
    { name: "himalayas", needs_key: false, key_stored: false, key_last4: null },
    { name: "adzuna", needs_key: true, key_stored: false, key_last4: null },
  ],
  keys_available: true,
};

export const onboardingDone = {
  completed_at: "2026-10-08T10:00:00+00:00",
  dismissed_at: null,
  profile_empty: false,
  done: ["profile", "looking", "where", "work"],
  passed: [],
  steps_left: [],
  open_wizard: false,
  show_card: false,
};

/** GET /api/profile/title-suggestions of an account without a model key: no idea yet. */
export const noTitleSuggestions = {
  status: "idle",
  needs: "model_key",
  suggestions: [],
  refreshed_at: null,
  error: null,
  note: null,
  messages: [],
  message_max: 300,
  asks_left: 3,
  asks_per_day: 3,
  estimate: null,
  cost_note: null,
};

/** GET /api/profile/about of a new account without a model key: the four parts, empty. */
export const emptyAbout = {
  text: "## Experience\n\n## Education\n\n## Skills\n\n## Answers\n",
  version: 0,
  updated_at: "2026-11-05T09:00:00Z",
  parts: ["Experience", "Education", "Skills", "Answers"].map((heading) => ({
    heading,
    key: heading.toLowerCase(),
    kind: heading.toLowerCase(),
    state: "in_step",
    duplicate: false,
    too_long: false,
    estimate: null,
  })),
  reading: null,
  undo: null,
  needs: "model_key",
  advanced: 0,
  missing_blocks: [],
  dones_left: 30,
  part_max: 20000,
  text_max: 300000,
};

export const handlers = [
  // The profile's About you document (profile/about-document.tsx), before the profile's own
  // routes: /api/profile/about is not a section.
  http.get("*/api/profile/about", () => HttpResponse.json(emptyAbout)),
  // The job title ideas under Job titles (profile/title-suggestions.tsx): none without a key.
  http.get("*/api/profile/title-suggestions", () =>
    HttpResponse.json(noTitleSuggestions),
  ),
  http.get("*/api/jobs", () => HttpResponse.json(emptyJobsPage)),
  http.get("*/api/jobs/sweeps", () => HttpResponse.json(emptySweeps)),
  // The job feeds, no Adzuna key stored (Find jobs and the Account page's Adzuna key card).
  http.get("*/api/jobs/feeds", () => HttpResponse.json(noFeedKeys)),
  // Find jobs' list of addresses: no address is a company job board, and no board ideas.
  http.post("*/api/jobs/boards/detect", () =>
    HttpResponse.json(
      { detail: "This address is not a company job board the app reads." },
      { status: 422 },
    ),
  ),
  http.get("*/api/jobs/boards/starter", () =>
    HttpResponse.json({ checked_on: "2026-10-30", boards: [] }),
  ),
  http.get("*/api/jobs/boards/suggestions", () =>
    HttpResponse.json({ boards: [] }),
  ),
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
  // The browser free, no session of the person (the Account page's websites card).
  http.get("*/api/browser", () =>
    HttpResponse.json({
      available: true,
      profile: { exists: false, size_bytes: 0, last_used_at: null },
      lease: null,
      holder: null,
      queue: [],
    }),
  ),
  // No website signed in (the Account page's Websites you are signed in to).
  http.get("*/api/browser/sites", () => HttpResponse.json({ sites: [] })),
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
  // A skills draft that starts nothing (the line beside Save skills stays empty).
  http.post("*/api/documents/spend/skills", () =>
    HttpResponse.json({
      auto: true,
      min_fit: 70,
      writes: false,
      newly: 0,
      newly_estimate: { tokens: { low: 0, high: 0 }, cost: null },
      priced: false,
      cost_note: "",
    }),
  ),
  // No skills change rescored yet (the line after Save skills).
  http.get("*/api/jobs/rescore/status", () =>
    HttpResponse.json({
      state: "idle",
      wanted_revision: null,
      done_revision: 0,
      finished_at: null,
      outcome: null,
    }),
  ),
  // No writing rule yet, the default of a new user (the Search tab's Writing rules card).
  http.get("*/api/documents/settings", () =>
    HttpResponse.json({ no_dash_separators: false, banned_phrases: [] }),
  ),
  // No application started for a posting (job detail's Apply section).
  http.get("*/api/applications", () => HttpResponse.json({ items: [] })),
  // A new account's apply settings (the job page's Apply line): the review box unticked.
  http.get("*/api/apply/settings", () =>
    HttpResponse.json(defaultApplySettings),
  ),
  // Nothing waits in the review queue (the app shell's Applications count).
  http.get("*/api/apply/queue/count", () =>
    HttpResponse.json({ waiting_on_you: 0 }),
  ),
  // The browser free (the job page's live view slot, apply A10a).
  http.get("*/api/browser", () =>
    HttpResponse.json({
      available: true,
      profile: { exists: false, size_bytes: 0, last_used_at: null },
      lease: null,
      holder: null,
      queue: [],
    }),
  ),
  // The first login wizard is done (opportunities O5): no redirect, no "Finish setting up".
  http.get("*/api/opportunities/onboarding", () =>
    HttpResponse.json(onboardingDone),
  ),
  http.get("*/api/auth/me", () =>
    HttpResponse.json({ detail: "Not signed in." }, { status: 401 }),
  ),
];

export const server = setupServer(...handlers);
