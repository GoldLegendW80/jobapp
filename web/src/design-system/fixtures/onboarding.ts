import { delay, http, HttpResponse } from "msw";

import type { OnboardingState } from "@/features/onboarding/api/onboarding-api";
import { EMPTY, type Search } from "@/features/profile/api/profile-api";
import { fakeDocuments } from "@/test/documents";
import { fakeProfile } from "@/test/profile";
import { defaultApplySettings, server } from "@/test/server";

/** A new member's first sign in: nothing answered, the wizard opens. */
export const firstSignIn: OnboardingState = {
  completed_at: null,
  dismissed_at: null,
  profile_empty: true,
  done: [],
  passed: [],
  steps_left: ["profile", "looking", "where", "work"],
  open_wizard: true,
  show_card: true,
};

/** The roles saved and the first steps passed: every step can open. */
export const rolesSaved: OnboardingState = {
  ...firstSignIn,
  profile_empty: false,
  done: ["looking"],
  passed: ["profile"],
  steps_left: ["where", "work"],
};

/** Every step answered: the summary is next. */
export const allAnswered: OnboardingState = {
  ...firstSignIn,
  profile_empty: false,
  done: ["profile", "looking", "where", "work"],
  passed: ["profile", "pay", "apply"],
  steps_left: [],
};

/** The wizard put off with Skip for now: no redirect, a card on the jobs page instead. */
export const putOff: OnboardingState = {
  ...firstSignIn,
  dismissed_at: "2026-11-06T09:30:00+00:00",
  open_wizard: false,
};

/** The answers of an invented data engineer, with two websites to search. */
export const savedSearch: Search = {
  ...EMPTY.search,
  title_include: {
    advanced_regex: false,
    terms: ["Data engineer", "Analytics engineer"],
  },
  location: { advanced_regex: false, terms: ["Lyon", "Remote Europe"] },
  remote_stance: "hybrid",
  contract_types: ["CDI", "Freelance"],
  interactive_sites: [
    {
      platform: "welcome_to_the_jungle",
      url: "https://www.welcometothejungle.com/",
      enabled: true,
      mode: "llm_browser",
    },
    {
      platform: "custom",
      url: "https://jobs.example.org/",
      enabled: true,
      mode: "llm_browser",
    },
  ],
};

/** A website's permission as the api answers it: shown with its risk sentence, never given yet. */
const permission = {
  enabled: false,
  accepted: false,
  allowed: false,
  text: "The website may limit or close an account it sees searched by a program. The app searches it in your own browser, slowly, and only while you allow it.",
  text_version: 1,
  query: null,
  paused_until: null,
};

/**
 * The wizard's api for one person: their profile (none, or only `search` answered), their wizard
 * state, and apply's settings. Steps passed, Skip for now and Start answer as the api does.
 */
export function fakeWizard({
  state = firstSignIn,
  search,
}: { state?: OnboardingState; search?: Search } = {}) {
  fakeProfile(search ? { search } : null);
  fakeDocuments();
  let current = { ...state };
  let settings: Record<string, unknown> = { ...defaultApplySettings };
  server.use(
    http.get("*/api/opportunities/onboarding", () =>
      HttpResponse.json(current),
    ),
    http.post("*/api/opportunities/onboarding/steps", async ({ request }) => {
      const { step } = (await request.json()) as { step: string };
      if (!current.passed.includes(step))
        current = { ...current, passed: [...current.passed, step] };
      return HttpResponse.json(current);
    }),
    http.post("*/api/opportunities/onboarding/dismiss", () => {
      current = {
        ...current,
        dismissed_at: "2026-11-06T09:30:00+00:00",
        open_wizard: false,
      };
      return HttpResponse.json(current);
    }),
    http.post("*/api/opportunities/onboarding/complete", () => {
      current = {
        ...current,
        completed_at: "2026-11-06T09:30:00+00:00",
        open_wizard: false,
        show_card: false,
      };
      return HttpResponse.json({
        state: current,
        sweep: search?.interactive_sites?.length ? "started" : "no_websites",
        sweep_id: null,
      });
    }),
    http.get("*/api/browser/linkedin", () => HttpResponse.json(permission)),
    http.get("*/api/opportunities/platforms/:kind", () =>
      HttpResponse.json(permission),
    ),
    http.get("*/api/apply/settings", () => HttpResponse.json(settings)),
    http.put("*/api/apply/settings", async ({ request }) => {
      settings = {
        ...settings,
        ...((await request.json()) as Record<string, unknown>),
      };
      return HttpResponse.json(settings);
    }),
  );
}

/** The profile cannot be read: the wizard has nothing to start from. */
export function failWizardProfile(how: "error" | "pending") {
  fakeWizard();
  server.use(
    http.get("*/api/profile", async () => {
      if (how === "pending") await delay("infinite");
      return HttpResponse.json(
        { detail: "The server had a problem. Try again in a moment." },
        { status: 500 },
      );
    }),
  );
}
