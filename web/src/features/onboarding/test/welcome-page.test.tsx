import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { matchRoutes } from "react-router";
import { afterEach, describe, expect, it } from "vitest";

import { fakeDeck } from "@/test/deck";
import { fakeSession, member } from "@/test/auth";
import { fakeDocuments as fakeCvDocuments } from "@/test/documents";
import { fakeDocument, fakeDocuments, fakePrompt } from "@/test/ingestion";
import { fakeAdditions, fakeRun, fakeRuns } from "@/test/ingestion-runs";
import { EMPTY, profileKey } from "@/features/profile/api/profile-api";
import { completeness, fakeProfile, type FakeProfile } from "@/test/profile";
import { renderApp } from "@/test/render";
import { onboardingDone, server } from "@/test/server";
import { routes } from "@/app/routes";

import type { ModelCredential } from "@/features/agents/api/agents-api";

import type { OnboardingState } from "../api/onboarding-api";

/** What the reading of step 3 proposes: one role to add, one headline that would replace. */
const proposal = (id: string, section: string, more = {}) => ({
  id,
  status: "pending",
  section,
  key: id,
  slot: id,
  action: "add",
  value: "Data engineer",
  before: null,
  after: "Data engineer",
  noop: false,
  stale: false,
  stale_reason: null,
  locked: false,
  bulk: null,
  problem: null,
  flags: [],
  free_text: false,
  citations: [],
  conflict: [],
  depends_on: [],
  base_revision: 0,
  applied_revision: null,
  ...more,
});
const BUILT_COUNTS = {
  total: 3,
  pending: 3,
  accepted: 0,
  rejected: 0,
  superseded: 0,
  invalid: 0,
  undone: 0,
};
const BUILT = {
  run_id: "r1",
  status: "done",
  partial: false,
  profile_revision: 0,
  counts: BUILT_COUNTS,
  documents: [],
  sections: [
    {
      section: "search",
      counts: BUILT_COUNTS,
      items: [
        proposal("p-role", "search"),
        proposal("p-headline", "identity", {
          action: "replace",
          bulk: "replace",
          before: "Engineer",
        }),
        // Two files disagree on the years: fill_empty lets the server take one.
        proposal("p-years", "skills", {
          bulk: "flagged",
          flags: ["conflict"],
        }),
      ],
    },
  ],
};

const fresh: OnboardingState = {
  completed_at: null,
  dismissed_at: null,
  profile_empty: true,
  done: [],
  passed: [],
  steps_left: ["profile", "looking", "where", "work"],
  open_wizard: true,
  show_card: true,
};

const SETTINGS = {
  mode_after_like: "review",
  full_auto_available: true,
  paused: false,
  min_fit: 85,
  min_fit_chosen: false,
  safety_ceiling: 20,
  hold_s: 900,
  hold_min_s: 900,
  company_days: 30,
  show_postings_from: null,
  write_documents_from: null,
  rehearsals: 3,
  rehearsals_done: 0,
  full_auto_since: null,
};

interface StoredSearch {
  title_include?: { terms: string[] };
  remote_stance?: string | null;
  contract_types?: string[];
}

/** What the api says the profile answers (onboarding.done_steps), from the fake profile. */
function doneOf(profile: FakeProfile): string[] {
  const search = profile.sections.search as StoredSearch | undefined;
  return [
    search?.title_include?.terms.length ? "looking" : "",
    search?.remote_stance ? "where" : "",
    search?.contract_types?.length ? "work" : "",
  ].filter(Boolean);
}

/**
 * The wizard's routes. With `profile`, `done` follows its saves and a step passed is kept in
 * its preferences without a new revision, as on the api.
 */
function fakeOnboarding(
  initial = fresh,
  settings = SETTINGS,
  profile?: FakeProfile,
) {
  const calls = {
    dismiss: 0,
    complete: 0,
    settings: [] as unknown[],
    steps: [] as string[],
  };
  let state = { ...initial };
  let kept: Record<string, unknown> = { ...settings };
  const current = () =>
    profile ? { ...state, done: [...state.done, ...doneOf(profile)] } : state;
  server.use(
    http.get("*/api/opportunities/onboarding", () =>
      HttpResponse.json(current()),
    ),
    http.post("*/api/opportunities/onboarding/steps", async ({ request }) => {
      const { step } = (await request.json()) as { step: string };
      calls.steps.push(step);
      if (!state.passed.includes(step)) {
        state = { ...state, passed: [...state.passed, step] };
        if (profile) {
          profile.sections.preferences = {
            ...EMPTY.preferences,
            onboarding: {
              ...EMPTY.preferences.onboarding,
              steps_passed: state.passed,
            },
          };
        }
      }
      return HttpResponse.json(current());
    }),
    http.post("*/api/opportunities/onboarding/dismiss", () => {
      calls.dismiss += 1;
      state = {
        ...state,
        dismissed_at: "2026-10-20T08:00:00+00:00",
        open_wizard: false,
      };
      return HttpResponse.json(current());
    }),
    http.post("*/api/opportunities/onboarding/complete", () => {
      calls.complete += 1;
      state = {
        ...state,
        completed_at: "2026-10-20T08:00:00+00:00",
        open_wizard: false,
        show_card: false,
      };
      return HttpResponse.json({
        state: current(),
        sweep: "no_websites",
        sweep_id: null,
      });
    }),
    http.get("*/api/apply/settings", () => HttpResponse.json(kept)),
    http.put("*/api/apply/settings", async ({ request }) => {
      const body = (await request.json()) as Record<string, unknown>;
      calls.settings.push(body);
      kept = { ...kept, ...body };
      return HttpResponse.json(kept);
    }),
  );
  return calls;
}

afterEach(() => {
  window.localStorage.clear();
  window.sessionStorage.clear();
});

// An invented key of the accepted shape; its last four characters are WXyz.
const KEY = `sk-ant-api03-${"Ab1_".repeat(20)}WXyz`;

/** GET, PUT and POST verify of /api/me/model-credential: none saved unless `initial`. */
function fakeKey(initial: ModelCredential | null = null) {
  const calls = { puts: [] as Array<Record<string, unknown>>, verifies: 0 };
  let saved = initial;
  server.use(
    http.get("*/api/me/model-credential", () =>
      saved
        ? HttpResponse.json(saved)
        : HttpResponse.json({ detail: "none" }, { status: 404 }),
    ),
    http.put("*/api/me/model-credential", async ({ request }) => {
      const body = (await request.json()) as Record<string, unknown>;
      calls.puts.push(body);
      saved = {
        kind: "anthropic_api_key",
        last4: String(body.secret).slice(-4),
        verified_at: null,
        updated_at: "2026-11-05T08:00:00Z",
      };
      return HttpResponse.json(saved);
    }),
    http.post("*/api/me/model-credential/verify", () => {
      calls.verifies += 1;
      if (!saved) return HttpResponse.json({}, { status: 404 });
      saved = { ...saved, verified_at: "2026-11-05T08:01:00Z" };
      return HttpResponse.json({
        result: "verified",
        message: "Anthropic accepted this key.",
        credential: saved,
      });
    }),
  );
  return calls;
}

/** The roles are saved: every step can open. */
const withRoles: OnboardingState = { ...fresh, done: ["looking"] };

const next = () => screen.getByRole("button", { name: /^Next/ });
const stepHeading = () =>
  screen.getByRole("heading", { level: 2, name: /^Step \d+ of 10/ });

describe("the first login redirect", () => {
  it.each([
    "/welcome?step=profile#upload",
    "/Welcome?step=profile#upload",
    "/%57elcome?step=profile#upload",
    "/WELCOME///?step=profile#upload",
  ])("stays on the actual wizard route %s", async (path) => {
    fakeSession(member);
    fakeProfile(null);
    fakeCvDocuments();
    const calls = fakeOnboarding();
    const user = userEvent.setup();
    const app = renderApp(path);
    await screen.findByRole("heading", { level: 1, name: "Welcome" });
    expect(app.location()).toBe(path);
    expect(document.documentElement.dataset.onboarding).toBe("stay");
    await user.click(screen.getByRole("button", { name: "Skip for now" }));
    await waitFor(() => {
      expect(app.location()).toBe("/jobs");
    });
    expect(calls.dismiss).toBe(1);
  });

  it.each(["/welcome-again", "/welcome/again", "/welcome%2F", "/%2557elcome"])(
    "keeps the router boundary for %s",
    async (pathname) => {
      const matches = matchRoutes(routes, { pathname });
      expect(matches?.some(({ route }) => route.path === "welcome")).toBe(
        false,
      );
      fakeSession(member);
      fakeProfile(null);
      fakeCvDocuments();
      fakeOnboarding();
      const app = renderApp(pathname);
      await screen.findByRole("heading", { level: 1, name: "Welcome" });
      expect(app.location()).toBe(
        `/welcome?${new URLSearchParams({ next: pathname }).toString()}`,
      );
      expect(document.documentElement.dataset.onboarding).toBe("welcome");
    },
  );

  it("sends a new member to /welcome once per session", async () => {
    fakeSession(member);
    fakeProfile(null);
    fakeOnboarding();
    const first = renderApp("/jobs");
    await waitFor(() => {
      expect(first.location()).toBe("/welcome");
    });
    // the decision the journeys read, kept after the move to /welcome
    await screen.findByRole("heading", { level: 1, name: "Welcome" });
    expect(document.documentElement.dataset.onboarding).toBe("welcome");
    first.unmount();
    expect(document.documentElement.dataset.onboarding).toBeUndefined();
    const again = renderApp("/jobs");
    await screen.findByRole("heading", { level: 1, name: "Jobs" });
    expect(again.location()).toBe("/jobs");
    expect(document.documentElement.dataset.onboarding).toBe("stay");
  });

  it("shows the shell around a skeleton while it checks, never the page asked for", async () => {
    fakeSession(member);
    fakeProfile(null);
    let release: () => void = () => {};
    const answered = new Promise<void>((resolve) => {
      release = resolve;
    });
    server.use(
      http.get("*/api/opportunities/onboarding", async () => {
        await answered;
        return HttpResponse.json({ ...fresh, open_wizard: false });
      }),
    );
    renderApp("/jobs");
    const skeleton = await screen.findByTestId("page-skeleton");
    expect(skeleton).toHaveAttribute("role", "status");
    expect(skeleton).toHaveTextContent("Loading");
    // The frame is there: the main navigation and the footer, around the skeleton.
    expect(screen.getByRole("navigation", { name: "Main" })).toBeVisible();
    expect(screen.getByRole("contentinfo")).toBeInTheDocument();
    expect(screen.getByRole("main")).toContainElement(skeleton);
    expect(
      screen.queryByRole("heading", { level: 1, name: "Jobs" }),
    ).toBeNull();
    release();
    await screen.findByRole("heading", { level: 1, name: "Jobs" });
    expect(screen.queryByTestId("page-skeleton")).toBeNull();
  });

  it("never sends a person the api does not send", async () => {
    fakeSession(member);
    const app = renderApp("/jobs");
    await screen.findByRole("heading", { level: 1, name: "Jobs" });
    // the default answer: finished, so no card either
    expect(screen.queryByText("Finish setting up")).toBeNull();
    expect(app.location()).toBe("/jobs");
  });

  it("sends nobody anywhere and shows no card when the api fails", async () => {
    fakeSession(member);
    fakeProfile(null);
    let asked = 0;
    server.use(
      http.get("*/api/opportunities/onboarding", () => {
        asked += 1;
        return HttpResponse.json(
          { detail: "Internal Server Error" },
          { status: 500 },
        );
      }),
    );
    const app = renderApp("/jobs?q=data");
    // the page asked for shows, the decision reads stay
    await screen.findByRole("heading", { level: 1, name: "Jobs" });
    expect(app.location()).toBe("/jobs?q=data");
    await waitFor(() => {
      expect(document.documentElement.dataset.onboarding).toBe("stay");
    });
    expect(asked).toBeGreaterThan(0);
    // the card stays hidden: no answer is no card
    expect(screen.queryByText("Finish setting up")).toBeNull();
    expect(screen.queryByRole("link", { name: "Continue setup" })).toBeNull();
    // and the gate does not ask again in this session
    app.unmount();
    const again = renderApp("/jobs");
    await screen.findByRole("heading", { level: 1, name: "Jobs" });
    expect(again.location()).toBe("/jobs");
    expect(document.documentElement.dataset.onboarding).toBe("stay");
  });

  it("shows the card on the home page while steps are left", async () => {
    fakeSession(member);
    fakeOnboarding({ ...fresh, open_wizard: false, dismissed_at: "x" });
    renderApp("/jobs");
    const card = await screen.findByRole("region", {
      name: "Finish setting up",
    });
    expect(
      within(card).getByRole("link", { name: "Continue setup" }),
    ).toHaveAttribute("href", "/welcome");
  });
});

describe("the wizard", () => {
  it("opens folded pay on an error, focuses it, and saves every figure after correction", async () => {
    window.localStorage.clear();
    fakeSession(member);
    const profile = fakeProfile(null);
    fakeOnboarding(withRoles);
    const user = userEvent.setup();
    renderApp("/welcome?step=pay");
    await screen.findByText(/Step 8 of 10/);
    const advanced = screen.getByRole("button", { name: "Advanced" });
    expect(advanced).toHaveAttribute("aria-expanded", "false");
    await user.click(advanced);
    const salary = screen.getByRole("group", { name: "Yearly salary" });
    await user.type(within(salary).getByLabelText("From"), "90000");
    await user.type(within(salary).getByLabelText("To"), "10000");
    const daily = screen.getByRole("group", { name: "Daily rate (freelance)" });
    await user.type(within(daily).getByLabelText("From"), "600");
    await user.type(within(daily).getByLabelText("To"), "800");
    await user.click(advanced);
    await user.click(next());
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "The first figure is the lower one.",
    );
    expect(advanced).toHaveAttribute("aria-expanded", "true");
    const reopened = screen.getByRole("group", { name: "Yearly salary" });
    expect(within(reopened).getByLabelText("From")).toHaveFocus();
    await user.clear(within(reopened).getByLabelText("To"));
    await user.type(within(reopened).getByLabelText("To"), "100000");
    await user.click(next());
    await screen.findByText(/Step 9 of 10/);
    expect(profile.saves.map((save) => save.section)).toEqual([
      "search",
      "answers",
    ]);
    expect(profile.saves[0]?.body.data).toMatchObject({
      compensation: {
        salary: { min: 90000, max: 100000, currency: "EUR" },
        daily_rate: { min: 600, max: 800, currency: "EUR" },
      },
    });
    // Forms get one range per currency: the yearly salary, from the revision the search gave.
    expect(profile.saves[1]?.body).toMatchObject({
      base_revision: 1,
      data: {
        salary_stance: "range",
        salary_ranges: [
          { currency: "EUR", minimum: 90000, maximum: 100000, period: "year" },
        ],
      },
    });
  });

  it("saves each step on Next, keeps the answers on Back, and resumes after a reload", async () => {
    fakeSession(member);
    const profile = fakeProfile(null);
    // step 1 skipped earlier, on this device or another: the profile keeps it
    const calls = fakeOnboarding(
      { ...fresh, passed: ["profile"] },
      SETTINGS,
      profile,
    );
    const user = userEvent.setup();
    const app = renderApp("/welcome?step=looking");
    await screen.findByLabelText("Roles");
    expect(stepHeading()).toHaveTextContent(
      "Step 4 of 10: What you are looking for",
    );
    expect(screen.queryByRole("button", { name: "Skip this step" })).toBeNull();

    await user.click(next());
    expect(await screen.findByRole("alert")).toHaveTextContent(
      /at least one role/,
    );
    expect(profile.saves).toEqual([]);

    await user.type(
      screen.getByLabelText("Roles"),
      "Data engineer, ML engineer",
    );
    await user.click(screen.getByRole("button", { name: "Advanced" }));
    await user.click(screen.getByRole("checkbox", { name: "Senior" }));
    await user.click(next());
    await screen.findByText(/Step 5 of 10/);
    expect(profile.saves).toHaveLength(1);
    expect(profile.saves[0]?.section).toBe("search");
    expect(profile.saves[0]?.body.base_revision).toBe(0);
    expect(profile.saves[0]?.body.data).toMatchObject({
      title_include: { terms: ["Data engineer", "ML engineer"] },
      seniority: ["senior"],
    });

    await user.click(screen.getByRole("checkbox", { name: "Anywhere" }));
    await user.click(screen.getByRole("radio", { name: "Hybrid" }));
    await user.click(next());
    await screen.findByRole("heading", {
      level: 2,
      name: "Step 6 of 10: Websites to search",
    });
    // each step passed is kept in the profile without a revision: the next save starts from
    // the revision the last save gave, with no read of the profile in between
    expect(calls.steps).toEqual(["looking", "where"]);
    expect(profile.saves[1]?.body.base_revision).toBe(1);

    await user.click(screen.getByRole("button", { name: /Back/ }));
    await user.click(screen.getByRole("button", { name: /Back/ }));
    expect(screen.getByLabelText("Roles")).toHaveValue(
      "Data engineer, ML engineer",
    );
    expect(screen.getByRole("checkbox", { name: "Senior" })).toBeChecked();
    app.unmount();

    // A closed tab, or another device, a new visit to /welcome: it opens at the first step left.
    const back = renderApp("/welcome");
    await screen.findByRole("heading", {
      level: 2,
      name: "Step 6 of 10: Websites to search",
    });
    expect(back.location()).toBe("/welcome");
  });

  it("shows the profile's dialog when another tab saved meanwhile", async () => {
    fakeSession(member);
    const profile = fakeProfile(null);
    fakeOnboarding(withRoles);
    const user = userEvent.setup();
    renderApp("/welcome?step=work");
    await screen.findByText(/Step 7 of 10/);
    profile.revision = 3; // saved from another tab
    await user.click(
      screen.getByRole("checkbox", { name: "Freelance mission" }),
    );
    await user.click(next());
    const dialog = await screen.findByRole("alertdialog");
    expect(dialog).toHaveTextContent("This section changed elsewhere");
    await user.click(
      within(dialog).getByRole("button", { name: "Keep editing" }),
    );
    expect(
      screen.getByRole("checkbox", { name: "Freelance mission" }),
    ).toBeChecked();
    await user.click(next());
    await screen.findByText(/Step 8 of 10/);
    expect(profile.saves.at(-1)?.body.base_revision).toBe(3);
  });

  it("points to Application settings for Full auto and never turns it on", async () => {
    fakeSession(member);
    const profile = fakeProfile(null);
    const calls = fakeOnboarding(withRoles);
    const user = userEvent.setup();
    renderApp("/welcome?step=apply");
    await screen.findByText(/Step 9 of 10/);
    const full = screen.getByRole("radio", { name: /Full auto/ });
    expect(full).toBeDisabled();
    expect(full).not.toBeChecked();
    expect(screen.queryByText("Available soon")).toBeNull();
    expect(
      screen.getByRole("link", { name: "Application settings" }),
    ).toHaveAttribute("href", "/settings/applications");
    expect(screen.getByText(/hold of at least 15 minutes/)).toBeInTheDocument();
    expect(screen.getByRole("radio", { name: /Review first/ })).toBeChecked();
    await user.click(screen.getByRole("radio", { name: "Warm" }));
    await user.click(next());
    await screen.findByText(/Step 10 of 10/);
    expect(calls.settings).toEqual([]); // the mode was not changed: nothing sent
    expect(profile.saves.at(-1)?.section).toBe("preferences");
    expect(profile.saves.at(-1)?.body.data).toMatchObject({
      documents_tone: "warm",
    });
  });

  it("keeps Full auto when a person who has it opens the wizard", async () => {
    fakeSession(member);
    fakeProfile(null);
    const calls = fakeOnboarding(withRoles, {
      ...SETTINGS,
      mode_after_like: "full_auto",
    });
    const user = userEvent.setup();
    renderApp("/welcome?step=apply");
    await screen.findByText(/Step 9 of 10/);
    await waitFor(() => {
      expect(screen.getByRole("radio", { name: /Full auto/ })).toBeChecked();
    });
    await user.click(next());
    await screen.findByText(/Step 10 of 10/);
    expect(calls.settings).toEqual([]);
  });

  it("warns on the summary while the list is empty, and opens Find jobs on Start", async () => {
    fakeSession(member);
    fakeProfile(null);
    const calls = fakeOnboarding(withRoles);
    fakeDeck([]);
    const user = userEvent.setup();
    const app = renderApp("/welcome?step=summary");
    await screen.findByText(/Step 10 of 10/);
    expect(screen.queryByRole("button", { name: "Skip this step" })).toBeNull();
    expect(
      screen.getByText(/Your list of websites is empty/),
    ).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Add websites" }));
    await screen.findByRole("heading", {
      level: 2,
      name: "Step 6 of 10: Websites to search",
    });
    app.unmount();

    const end = renderApp("/welcome?step=summary");
    await screen.findByText(/Step 10 of 10/);
    await user.click(screen.getByRole("button", { name: "Start" }));
    // Nothing to search (the api answers no_websites): Find jobs opens, not an empty deck.
    expect(
      await screen.findByRole("dialog", { name: "Find jobs" }),
    ).toBeVisible();
    expect(end.location()).toBe("/jobs");
    expect(calls.complete).toBe(1);
  });

  it("opens Discover on Start once a search started", async () => {
    fakeSession(member);
    fakeProfile(null);
    const calls = fakeOnboarding(withRoles);
    server.use(
      http.post("*/api/opportunities/onboarding/complete", () => {
        calls.complete += 1;
        return HttpResponse.json({
          state: { ...withRoles, completed_at: "2026-10-20T08:00:00+00:00" },
          sweep: "started",
          sweep_id: "00000000-0000-4000-8000-000000000001",
        });
      }),
    );
    fakeDeck([]);
    const user = userEvent.setup();
    const end = renderApp("/welcome?step=summary");
    await screen.findByText(/Step 10 of 10/);
    await user.click(screen.getByRole("button", { name: "Start" }));
    await waitFor(() => {
      expect(end.location()).toBe("/discover");
    });
    expect(calls.complete).toBe(1);
  });

  it("adds a website in Websites to search and saves it on Next, without telling the api the step", async () => {
    fakeSession(member);
    const profile = fakeProfile(null);
    const calls = fakeOnboarding(withRoles, SETTINGS, profile);
    server.use(
      http.get("*/api/browser/sites", () => HttpResponse.json({ sites: [] })),
    );
    const user = userEvent.setup();
    renderApp("/welcome?step=sites");
    await screen.findByRole("heading", {
      level: 2,
      name: "Step 6 of 10: Websites to search",
    });
    const region = screen.getByRole("region", { name: "Where to search" });
    await user.type(
      within(region).getByLabelText("Website address"),
      "careers.example.org",
    );
    await user.click(within(region).getByRole("button", { name: "Add" }));
    expect(
      await within(region).findByRole("button", {
        name: "Remove careers.example.org",
      }),
    ).toBeVisible();
    await user.click(next());
    await screen.findByText(/Step 7 of 10/);
    expect(profile.saves.at(-1)?.section).toBe("search");
    expect(profile.saves.at(-1)?.body.data).toMatchObject({
      interactive_sites: [
        {
          platform: "custom",
          url: "https://careers.example.org",
          enabled: true,
          mode: "llm_browser",
        },
      ],
    });
    expect(calls.steps).not.toContain("sites");
  });

  it("offers no starter list on the summary, and counts the places of the list", async () => {
    fakeSession(member);
    const profile = fakeProfile(null);
    profile.sections.search = {
      ...EMPTY.search,
      interactive_sites: [
        {
          platform: "welcome_to_the_jungle",
          url: "https://www.welcometothejungle.com/",
          enabled: true,
          mode: "llm_browser",
        },
        {
          platform: "indeed",
          url: "https://fr.indeed.com/",
          enabled: false,
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
    fakeOnboarding(withRoles, SETTINGS, profile);
    let reads = 0;
    server.use(
      http.get("*/api/opportunities/starter", () => {
        reads += 1;
        return HttpResponse.json({});
      }),
    );
    renderApp("/welcome?step=summary");
    await screen.findByText(/Step 10 of 10/);
    expect(
      await screen.findByText(
        /Start runs your first search on 2 places from your list/,
      ),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("region", { name: "Boards and sites to search" }),
    ).toBeNull();
    expect(screen.queryByText(/board/i)).toBeNull();
    expect(reads).toBe(0);
  });

  it("Skip for now puts it off and goes home", async () => {
    fakeSession(member);
    fakeProfile(null);
    const calls = fakeOnboarding();
    const user = userEvent.setup();
    const app = renderApp("/welcome?step=profile");
    const skip = await screen.findByRole("button", { name: "Skip for now" });
    const read = app.client.getQueryState(profileKey)?.dataUpdateCount ?? 0;
    await user.click(skip);
    await waitFor(() => {
      expect(app.location()).toBe("/jobs");
    });
    expect(calls.dismiss).toBe(1);
    // putting it off is a revision of the profile: the cached one is read again before any save
    await waitFor(() => {
      const state = app.client.getQueryState(profileKey);
      expect(
        state?.isInvalidated === true || (state?.dataUpdateCount ?? 0) > read,
      ).toBe(true);
    });
    expect(
      await screen.findByRole("region", { name: "Finish setting up" }),
    ).toBeVisible();
  });

  it("shows what was saved in the summary, never what was typed then skipped", async () => {
    fakeSession(member);
    const profile = fakeProfile(null);
    fakeOnboarding(withRoles, SETTINGS, profile);
    const user = userEvent.setup();
    renderApp("/welcome?step=where");
    await screen.findByText(/Step 5 of 10/);
    await user.type(screen.getByLabelText("Places"), "Lyon");
    await user.click(screen.getByRole("button", { name: "Skip this step" }));
    await screen.findByRole("heading", {
      level: 2,
      name: "Step 6 of 10: Websites to search",
    });
    await user.click(screen.getByRole("button", { name: "Skip this step" }));
    await screen.findByText(/Step 7 of 10/);
    await user.click(screen.getByRole("button", { name: "Skip this step" }));
    await screen.findByText(/Step 8 of 10/);
    await user.click(screen.getByRole("button", { name: "Advanced" }));
    const [from] = screen.getAllByLabelText("From");
    const [to] = screen.getAllByLabelText("To");
    if (!from || !to) throw new Error("no salary fields");
    await user.type(from, "50000");
    await user.type(to, "60000");
    await user.click(screen.getByRole("button", { name: "Skip this step" }));
    await screen.findByText(/Step 9 of 10/);
    await user.click(screen.getByRole("button", { name: "Skip this step" }));
    await screen.findByText(/Step 10 of 10/);
    const summary = screen.getByRole("region", { name: /Summary/ });
    expect(summary).toHaveTextContent("Not given");
    expect(summary).not.toHaveTextContent("50000");
    expect(summary).toHaveTextContent("No place yet");
    expect(summary).not.toHaveTextContent("Lyon");
    expect(profile.saves.filter((save) => save.section === "search")).toEqual(
      [],
    );
  });

  it("asks the answers forms need, keeps every other stored answer, and reaches a complete Answers", async () => {
    fakeSession(member);
    const stored = {
      ...EMPTY.answers,
      relocation: "Open to Lyon.",
      eeo_policy: "stored",
      eeo_answers: [{ topic: "gender", answer: "Woman" }],
      salary_ranges: [
        { currency: "USD", minimum: 90000, maximum: null, period: "year" },
      ],
    };
    const profile = fakeProfile({
      identity: { ...EMPTY.identity, country: "France" },
      search: {
        ...EMPTY.search,
        title_include: { terms: ["Data engineer"], advanced_regex: false },
      },
      answers: stored,
    });
    fakeOnboarding(withRoles, SETTINGS, profile);
    const user = userEvent.setup();
    renderApp("/welcome?step=looking");
    await user.type(
      await screen.findByLabelText("Why you are looking"),
      "I want to build data platforms.",
    );
    await user.click(next());
    await screen.findByText(/Step 5 of 10/);
    await user.click(screen.getByRole("checkbox", { name: "Anywhere" }));
    await user.click(screen.getByRole("radio", { name: "Remote" }));
    await user.click(next());
    await screen.findByText(/Step 6 of 10/);
    await user.click(screen.getByRole("button", { name: "Skip this step" }));
    await screen.findByText(/Step 7 of 10/);
    // Every answer may stay empty; a choice needs its country.
    await user.click(
      within(
        screen.getByRole("group", { name: "May you work there?" }),
      ).getByText("Yes"),
    );
    await user.click(next());
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Name the country or region where you may work",
    );
    await user.click(
      screen.getByRole("button", { name: "Notice period: 1 month" }),
    );
    await user.click(
      screen.getByRole("button", {
        name: "Earliest start: As soon as possible",
      }),
    );
    await user.click(
      screen.getByRole("button", {
        name: "Country or region: France, from your profile",
      }),
    );
    await user.click(
      within(
        screen.getByRole("group", { name: "Do you need a visa sponsorship?" }),
      ).getByText("No"),
    );
    await user.click(next());
    await screen.findByText(/Step 8 of 10/);
    await user.click(screen.getByRole("checkbox", { name: "Rather not say" }));
    await user.click(next());
    await screen.findByText(/Step 9 of 10/);
    await user.click(next());
    await screen.findByText(/Step 10 of 10/);

    // Each step saves its search, then its answers merged into the stored ones.
    expect(profile.saves.map((save) => save.section)).toEqual([
      "search",
      "answers",
      "search",
      "search",
      "answers",
      "search",
      "answers",
      "preferences",
    ]);
    expect(profile.sections.answers).toEqual({
      ...stored,
      motivation: "I want to build data platforms.",
      notice_period: "1 month",
      earliest_start: "As soon as possible",
      work_authorization: [
        { country: "France", answer: "Yes", needs_sponsorship: false },
      ],
      salary_stance: "leave_blank",
    });
    const summary = screen.getByRole("region", { name: /Summary/ });
    expect(summary).toHaveTextContent(
      "Why you are looking: I want to build data platforms.",
    );
    expect(summary).toHaveTextContent(
      "Notice period: 1 month; earliest start: As soon as possible",
    );
    expect(summary).toHaveTextContent(
      "Work authorisation: France, you may work there, no visa sponsorship needed",
    );
    expect(summary).toHaveTextContent("Rather not say");
    expect(summary).not.toHaveTextContent("Still to give");
    // The api's rules: Anywhere is a place, and every answer it requires is given.
    const done = completeness(profile.sections).sections;
    expect(done.answers?.missing).toEqual([]);
    expect(done.search?.missing).toEqual(["interactive_sites"]);
  });

  it("says on the summary which answers for forms are still to give", async () => {
    fakeSession(member);
    fakeProfile(null);
    fakeOnboarding(withRoles);
    renderApp("/welcome?step=summary");
    await screen.findByText(/Step 10 of 10/);
    const summary = screen.getByRole("region", { name: /Summary/ });
    expect(summary).toHaveTextContent("Why you are looking: not given yet");
    expect(summary).toHaveTextContent(
      "Notice period: not given yet; earliest start: not given yet",
    );
    expect(summary).toHaveTextContent("Work authorisation: not given yet");
    expect(summary).toHaveTextContent(
      "Still to give for a complete profile: why you are looking, notice period, earliest start, work authorisation, pay.",
    );
  });

  it("returns to the summary after a Change", async () => {
    fakeSession(member);
    const profile = fakeProfile(null);
    fakeOnboarding(withRoles, SETTINGS, profile);
    const user = userEvent.setup();
    renderApp("/welcome?step=summary");
    await screen.findByText(/Step 10 of 10/);
    await user.click(screen.getByRole("button", { name: "Change Where" }));
    await screen.findByText(/Step 5 of 10/);
    await user.click(screen.getByRole("checkbox", { name: "Anywhere" }));
    await user.click(screen.getByRole("radio", { name: "Remote" }));
    await user.click(next());
    await screen.findByText(/Step 10 of 10/);
    expect(screen.getByRole("region", { name: /Summary/ })).toHaveTextContent(
      "Anywhere; Remote",
    );
  });

  it("never reaches Start from the address while no role is saved", async () => {
    fakeSession(member);
    fakeProfile(null);
    fakeOnboarding({ ...fresh, passed: ["profile"] });
    renderApp("/welcome?step=summary");
    await screen.findByText(/Step 3 of 10/);
    expect(screen.queryByRole("button", { name: "Start" })).toBeNull();
  });

  it("keeps a deep link opened at first sign in and opens it at the end", async () => {
    fakeSession(member);
    fakeProfile(null);
    fakeOnboarding();
    const user = userEvent.setup();
    const app = renderApp("/applications?tab=sent");
    await waitFor(() => {
      expect(app.location()).toBe("/welcome?next=%2Fapplications%3Ftab%3Dsent");
    });
    // the page asked for never rendered before the move
    expect(
      screen.queryByRole("heading", { level: 1, name: "Applications" }),
    ).toBeNull();
    await user.click(
      await screen.findByRole("button", { name: "Skip this step" }),
    );
    await screen.findByText(/Step 2 of 10/);
    expect(app.location()).toContain("next=%2Fapplications%3Ftab%3Dsent");
    await user.click(screen.getByRole("button", { name: "Skip for now" }));
    await waitFor(() => {
      expect(app.location()).toBe("/applications?tab=sent");
    });
  });

  it("takes the first PDF as the CV without a click, and converts it once read, even from the next step", async () => {
    fakeSession(member);
    fakeProfile(null);
    fakeOnboarding();
    const cvs = fakeCvDocuments({ consent: { accepted: false } });
    const files = fakeDocuments();
    fakePrompt();
    fakeRuns();
    const user = userEvent.setup();
    renderApp("/welcome?step=profile");
    await screen.findByText(/Step 2 of 10/);
    expect(screen.queryByTestId("upload-cv-file")).toBeNull();
    await user.upload(screen.getByTestId("document-files"), [
      new File(["%PDF-1.7 Morgan Exampleton"], "Morgan CV.pdf", {
        type: "application/pdf",
      }),
      new File(["Projects"], "notes.md", { type: "text/markdown" }),
    ]);
    // Sent again with the mark, without a click: the server keeps its bytes for the look.
    await waitFor(() => {
      expect(files.uploads).toHaveLength(3);
    });
    expect(
      files.uploads
        .filter((one) => one.purpose === "base_cv")
        .map((one) => one.filename),
    ).toEqual([expect.stringContaining("Morgan%20CV.pdf")]);
    expect(await screen.findByText("Your CV")).toBeVisible();
    expect(screen.queryByRole("button", { name: /^This is my CV/ })).toBeNull();

    // The person moves on before the file is read: the conversion still follows, and the
    // server holds it until the consent.
    await user.click(screen.getByRole("button", { name: "Skip this step" }));
    await screen.findByText(/Step 3 of 10/);
    files.items.find((one) => one.name === "Morgan CV.pdf")!.status = "done";
    await waitFor(
      () => {
        expect(
          cvs.requests.filter(
            (call) => call.method === "POST" && call.path.endsWith("/base-cvs"),
          ),
        ).toHaveLength(1);
      },
      { timeout: 5000 },
    );
    const asked = cvs.requests.find(
      (call) => call.method === "POST" && call.path.endsWith("/base-cvs"),
    );
    expect(asked?.body).toMatchObject({ from: "upload" });
  });

  it("keeps the analysis of the computer under Advanced in Your profile", async () => {
    fakeSession(member);
    fakeProfile(null);
    fakeOnboarding();
    fakeDocuments();
    fakePrompt();
    const user = userEvent.setup();
    renderApp("/welcome?step=profile");
    await screen.findByText(/Step 2 of 10/);
    expect(
      screen.queryByRole("button", { name: "Copy the prompt" }),
    ).toBeNull();
    await user.click(screen.getByRole("button", { name: "Advanced" }));
    expect(
      await screen.findByRole("button", { name: "Copy the prompt" }),
    ).toBeVisible();
    expect(
      screen.getByRole("heading", { name: "Analyze your computer" }),
    ).toBeVisible();
  });

  it("builds the profile in step 3 once the box is ticked, writes what adds to it, and the roles show it", async () => {
    fakeSession(member);
    const profile = fakeProfile(null);
    fakeOnboarding();
    fakeDocuments([
      fakeDocument({ id: "d1", name: "cv.md" }),
      fakeDocument({ id: "d2", name: "notes.md" }),
    ]);
    const runs = fakeRuns({
      created: fakeRun({
        id: "r1",
        status: "done",
        finished_at: "2026-11-07T10:05:00Z",
      }),
    });
    const additions = fakeAdditions(
      "r1",
      BUILT,
      {
        decided: ["p-role", "p-years"],
        skipped: [{ id: "p-headline", reason: "replace" }],
        stopped: [],
        pending: 1,
      },
      () => {
        // What the server writes: the role the reading found.
        profile.sections.search = {
          ...EMPTY.search,
          title_include: { terms: ["Data engineer"], advanced_regex: false },
        };
        profile.revision += 1;
      },
    );
    const user = userEvent.setup();
    renderApp("/welcome?step=building");
    await screen.findByText(/Step 3 of 10/);
    expect(stepHeading()).toHaveTextContent(
      "Step 3 of 10: Building your profile",
    );
    const build = await screen.findByRole("button", {
      name: "Build my profile",
    });
    // Nothing is sent before the person agrees to what is.
    expect(build).toBeDisabled();
    await user.click(
      screen.getByRole("checkbox", {
        name: "I have read this and I agree to send the selected documents",
      }),
    );
    await waitFor(() => {
      expect(build).toBeEnabled();
    });
    await user.click(build);
    expect(
      await screen.findByText("Your profile is ready: 2 facts added."),
    ).toBeVisible();
    expect(runs.sent("POST", "/api/ingestion/runs")[0]?.body).toMatchObject({
      document_ids: ["d1", "d2"],
    });
    // Every pending item is sent: the server takes what adds or fills an empty place.
    expect(additions.accepted).toEqual([
      { proposal_ids: ["p-role", "p-headline", "p-years"], fill_empty: true },
    ]);
    expect(
      screen.getByText(/1 more waits for you in/).closest("p"),
    ).toHaveTextContent(
      "1 more waits for you in Fill my profile: it would change a value you already have.",
    );
    await user.click(next());
    await screen.findByText(/Step 4 of 10/);
    expect(screen.getByLabelText("Roles")).toHaveValue("Data engineer");
  });

  it("holds Next while the reading runs, and Skip still goes on", async () => {
    fakeSession(member);
    fakeProfile(null);
    fakeOnboarding();
    fakeDocuments([fakeDocument({ id: "d1", name: "cv.md" })]);
    fakeRuns({ runs: [fakeRun({ id: "r1", status: "running" })] });
    const user = userEvent.setup();
    renderApp("/welcome?step=building");
    expect(
      await screen.findByText(/^Reading your files: \d+ of \d+ parts$/),
    ).toBeVisible();
    expect(next()).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "Skip this step" }));
    await screen.findByText(/Step 4 of 10/);
  });

  it("explains a Like, and keeps the Apply click's review box in apply's settings", async () => {
    fakeSession(member);
    const profile = fakeProfile(null);
    const calls = fakeOnboarding(withRoles);
    const user = userEvent.setup();
    renderApp("/welcome?step=apply");
    await screen.findByText(/Step 9 of 10/);
    const like = screen.getByRole("group", { name: "After a Like" });
    expect(like).toHaveAccessibleDescription(
      "A Like on a posting in Discover asks the app to write your CV and letter for it and to prepare the application.",
    );
    expect(like).not.toHaveTextContent("You approve every application");
    expect(
      within(like).getByRole("radio", { name: /Review first/ }),
    ).toHaveAccessibleName(/a Like prepares/);
    const click = screen.getByRole("group", {
      name: "When you click Apply on a posting",
    });
    const box = within(click).getByRole("checkbox", {
      name: "Let me review each filled form before it is sent",
    });
    // Off by default (decisions, 2026-11-01): Apply sends the filled form.
    expect(box).not.toBeChecked();
    expect(box).toHaveAccessibleDescription(
      "When it is off, Apply fills the form and sends it for you. When it is on, you check each filled form and send it yourself.",
    );
    await user.click(box);
    await user.click(next());
    await screen.findByText(/Step 10 of 10/);
    // Only what changed, through apply's own route; the Like's mode is left as it was.
    expect(calls.settings).toEqual([{ review_before_send: true }]);
    expect(profile.saves.at(-1)?.section).toBe("preferences");
    const summary = screen.getByRole("region", { name: /Summary/ });
    expect(summary).toHaveTextContent(
      "Review first after a Like; Apply waits for you to review each filled form; documents in",
    );

    // Back on the step, the box reads the saved setting; Next with nothing changed sends nothing.
    await user.click(screen.getByRole("button", { name: /Back/ }));
    await screen.findByText(/Step 9 of 10/);
    await user.click(screen.getByRole("button", { name: /Back/ }));
    await screen.findByText(/Step 8 of 10/);
    await user.click(screen.getByRole("button", { name: "Skip this step" }));
    await screen.findByText(/Step 9 of 10/);
    expect(
      screen.getByRole("checkbox", {
        name: "Let me review each filled form before it is sent",
      }),
    ).toBeChecked();
  });

  it("says both choices of step 7 in the summary, as saved", async () => {
    fakeSession(member);
    fakeProfile(null);
    fakeOnboarding(withRoles, {
      ...SETTINGS,
      mode_after_like: "full_auto",
    });
    renderApp("/welcome?step=summary");
    await screen.findByText(/Step 10 of 10/);
    expect(screen.getByRole("region", { name: /Summary/ })).toHaveTextContent(
      "Full auto after a Like; Apply sends each filled form for you; documents in",
    );
  });

  it("asks for no model key on step 1, and says what an upload does without one", async () => {
    fakeSession(member);
    fakeProfile(null);
    fakeCvDocuments();
    fakeOnboarding();
    fakeKey();
    renderApp("/welcome?step=profile");
    await screen.findByText(/Step 2 of 10/);
    expect(
      await screen.findByText(
        /Turning it into a CV you can edit uses your Anthropic API key: until one is saved, that part waits/,
      ),
    ).toBeInTheDocument();
    expect(screen.queryByText(/model of your credential/)).toBeNull();
    expect(screen.queryByText(/No key is saved yet/)).toBeNull();
    expect(screen.queryByLabelText("API key")).toBeNull();
  });

  it("opens on Model access while none is saved, and saves a key masked as Account does", async () => {
    fakeSession(member);
    fakeProfile(null);
    fakeOnboarding();
    const key = fakeKey();
    const user = userEvent.setup();
    renderApp("/welcome");
    await screen.findByText(/Step 1 of 10/);
    expect(stepHeading()).toHaveTextContent("Step 1 of 10: Model access");
    const form = screen.getByRole("form", { name: "Model access" });
    expect(
      within(form).getByRole("radio", { name: "Claude subscription token" }),
    ).toBeInTheDocument();
    const field = within(form).getByLabelText("API key");
    expect(field).toHaveAttribute("type", "password");
    await user.type(field, KEY);
    await user.click(
      within(form).getByRole("button", { name: "Save credential" }),
    );
    // Checked with Anthropic at once: the field says so, in green.
    await waitFor(() => {
      expect(screen.getByTestId("credential-status")).toHaveTextContent(
        "Anthropic API key ending in WXyz, verified.",
      );
    });
    expect(key.verifies).toBe(1);
    expect(key.puts).toEqual([{ kind: "anthropic_api_key", secret: KEY }]);
    expect(document.body.innerHTML).not.toContain(KEY.slice(0, 20));
    // Saved here: the step stays open to show it, until Next.
    expect(stepHeading()).toHaveTextContent("Step 1 of 10: Model access");
    // Skippable all the same: reading, writing and searching wait for a credential.
    expect(
      screen.getByRole("button", { name: "Skip this step" }),
    ).toBeInTheDocument();
  });

  it("opens past Model access once a credential is saved, and says which in the summary", async () => {
    fakeSession(member);
    fakeProfile(null);
    fakeKey({
      kind: "claude_subscription",
      last4: "WXyz",
      verified_at: null,
      updated_at: "2026-11-01T08:00:00Z",
    });
    fakeOnboarding();
    const first = renderApp("/welcome");
    await screen.findByText(/Step 2 of 10/);
    expect(stepHeading()).toHaveTextContent("Step 2 of 10: Your profile");
    first.unmount();

    fakeOnboarding(withRoles);
    renderApp("/welcome?step=summary");
    await screen.findByText(/Step 10 of 10/);
    expect(screen.getByRole("region", { name: /Summary/ })).toHaveTextContent(
      "Claude subscription token ending in WXyz",
    );
  });

  it("says no credential is saved in the summary, and Start works without one", async () => {
    fakeSession(member);
    fakeProfile(null);
    fakeKey();
    const calls = fakeOnboarding(withRoles);
    fakeDeck([]);
    const user = userEvent.setup();
    const end = renderApp("/welcome?step=summary");
    await screen.findByText(/Step 10 of 10/);
    expect(screen.getByRole("region", { name: /Summary/ })).toHaveTextContent(
      "None saved yet: reading, writing and searching wait for it",
    );
    expect(screen.queryByLabelText("API key")).toBeNull();
    await user.click(screen.getByRole("button", { name: "Start" }));
    await waitFor(() => {
      expect(end.location()).toBe("/jobs");
    });
    expect(calls.complete).toBe(1);
  });

  it("shows only the bar and the heading on a narrow page, every name in the side rail on a wide one", async () => {
    fakeSession(member);
    fakeProfile(null);
    fakeOnboarding({ ...withRoles, passed: ["profile", "looking"] });
    renderApp("/welcome?step=where");
    await screen.findByText(/Step 5 of 10/);
    expect(stepHeading()).toHaveTextContent("Step 5 of 10: Where");
    const bar = screen.getByRole("progressbar", { name: "Wizard progress" });
    expect(bar).toHaveAttribute("aria-valuetext", "Step 5 of 10: Where");
    // The step is the value, 1 to 10, not the share of the bar that is filled.
    expect(bar).toHaveAttribute("aria-valuenow", "5");
    expect(bar).toHaveAttribute("aria-valuemin", "1");
    expect(bar).toHaveAttribute("aria-valuemax", "10");
    // The names are display none on a narrow page (Tailwind's hidden) and a column in the side
    // rail once the body is wide enough for one.
    const names = screen.getByTestId("welcome-step-names");
    expect(names).toHaveClass("hidden", "@4xl:flex");
    const labels = within(names)
      .getAllByRole("listitem")
      .map((item) => item.firstElementChild as HTMLElement);
    expect(labels).toHaveLength(10);
    for (const label of labels)
      expect(label).toHaveClass(
        "inline-flex",
        "min-h-8",
        "items-center",
        "leading-5",
        "whitespace-nowrap",
      );
    // The current one is marked; the steps passed open.
    const current = within(names).getByText("Where");
    expect(current).toHaveAttribute("aria-current", "step");
    expect(current.tagName).toBe("SPAN");
    expect(
      within(names).getByRole("button", { name: "What you are looking for" }),
    ).toBeInTheDocument();
  });

  it("uses no dash as a separator in what it shows", async () => {
    fakeSession(member);
    fakeProfile(null);
    fakeOnboarding({ ...onboardingDone, open_wizard: false });
    for (const step of [
      "looking",
      "where",
      "sites",
      "work",
      "pay",
      "apply",
      "summary",
    ]) {
      const app = renderApp(`/welcome?step=${step}`);
      await screen.findByText(/Step \d+ of 10/);
      expect(document.body.textContent).not.toMatch(/[–—]| - /);
      app.unmount();
    }
  });
});
