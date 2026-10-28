import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { matchRoutes } from "react-router";
import { afterEach, describe, expect, it } from "vitest";

import { fakeDeck } from "@/features/opportunities/deck/deck-test-api";
import { fakeSession, member } from "@/test/auth";
import { fakeDocuments as fakeCvDocuments } from "@/test/documents";
import { fakeDocument, fakeDocuments, fakePrompt } from "@/test/ingestion";
import { CONSENT_TEXT, fakeRuns, REMINDER } from "@/test/ingestion-runs";
import { EMPTY, profileKey } from "@/features/profile/api";
import { fakeProfile, type FakeProfile } from "@/test/profile";
import { renderApp } from "@/test/render";
import { onboardingDone, server } from "@/test/server";
import { routes } from "@/routes";

import type { OnboardingState } from "./onboarding-api";

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
    http.get("*/api/apply/settings", () => HttpResponse.json(settings)),
    http.put("*/api/apply/settings", async ({ request }) => {
      calls.settings.push(await request.json());
      return HttpResponse.json(settings);
    }),
  );
  return calls;
}

afterEach(() => {
  window.localStorage.clear();
  window.sessionStorage.clear();
});

/** The roles are saved: every step can open. */
const withRoles: OnboardingState = { ...fresh, done: ["looking"] };

const next = () => screen.getByRole("button", { name: /^Next/ });
const stepHeading = () =>
  screen.getByRole("heading", { level: 2, name: /^Step \d of 7/ });

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
    await screen.findByText(/Step 5 of 7/);
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
    await screen.findByText(/Step 6 of 7/);
    expect(profile.saves.at(-1)?.body.data).toMatchObject({
      compensation: {
        salary: { min: 90000, max: 100000, currency: "EUR" },
        daily_rate: { min: 600, max: 800, currency: "EUR" },
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
      "Step 2 of 7: What you are looking for",
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
    await screen.findByText(/Step 3 of 7/);
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
    await screen.findByText(/Step 4 of 7/);
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
    await screen.findByText(/Step 4 of 7/);
    expect(back.location()).toBe("/welcome");
  });

  it("shows the profile's dialog when another tab saved meanwhile", async () => {
    fakeSession(member);
    const profile = fakeProfile(null);
    fakeOnboarding(withRoles);
    const user = userEvent.setup();
    renderApp("/welcome?step=work");
    await screen.findByText(/Step 4 of 7/);
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
    await screen.findByText(/Step 5 of 7/);
    expect(profile.saves.at(-1)?.body.base_revision).toBe(3);
  });

  it("offers Full auto only as Available soon and never turns it on", async () => {
    fakeSession(member);
    const profile = fakeProfile(null);
    const calls = fakeOnboarding(withRoles);
    const user = userEvent.setup();
    renderApp("/welcome?step=apply");
    await screen.findByText(/Step 6 of 7/);
    const full = screen.getByRole("radio", { name: /Full auto/ });
    expect(full).toBeDisabled();
    expect(full).not.toBeChecked();
    expect(screen.getByText("Available soon")).toBeInTheDocument();
    expect(screen.getByText(/hold of at least 15 minutes/)).toBeInTheDocument();
    expect(screen.getByRole("radio", { name: /Review first/ })).toBeChecked();
    await user.click(screen.getByRole("radio", { name: "Warm" }));
    await user.click(next());
    await screen.findByText(/Step 7 of 7/);
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
    await screen.findByText(/Step 6 of 7/);
    await waitFor(() => {
      expect(screen.getByRole("radio", { name: /Full auto/ })).toBeChecked();
    });
    await user.click(next());
    await screen.findByText(/Step 7 of 7/);
    expect(calls.settings).toEqual([]);
  });

  it("ends on Start with the summary, and opens Discover", async () => {
    fakeSession(member);
    fakeProfile(null);
    const calls = fakeOnboarding(withRoles);
    fakeDeck([]);
    const user = userEvent.setup();
    const app = renderApp("/welcome?step=summary");
    await screen.findByText(/Step 7 of 7/);
    expect(screen.queryByRole("button", { name: "Skip this step" })).toBeNull();
    expect(
      screen.getByText(/covers 0 job websites ticked in Find jobs/),
    ).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Change Where" }));
    await screen.findByText(/Step 3 of 7/);
    await user.click(screen.getByRole("button", { name: /Next/ }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/place/);
    app.unmount();

    const end = renderApp("/welcome?step=summary");
    await screen.findByText(/Step 7 of 7/);
    await user.click(screen.getByRole("button", { name: "Start" }));
    await waitFor(() => {
      expect(end.location()).toBe("/discover");
    });
    expect(calls.complete).toBe(1);
  });

  // Owner, 2026-10-28: every search runs the job websites ticked in Find jobs alone, so the
  // summary offers no starter list of boards and counts the websites.
  it("offers no starter list on the summary, and counts the ticked job websites", async () => {
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
    await screen.findByText(/Step 7 of 7/);
    expect(
      await screen.findByText(/covers 2 job websites ticked in Find jobs/),
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
    await screen.findByText(/Step 3 of 7/);
    await user.type(screen.getByLabelText("Places"), "Lyon");
    await user.click(screen.getByRole("button", { name: "Skip this step" }));
    await screen.findByText(/Step 4 of 7/);
    await user.click(screen.getByRole("button", { name: "Skip this step" }));
    await screen.findByText(/Step 5 of 7/);
    await user.click(screen.getByRole("button", { name: "Advanced" }));
    const [from] = screen.getAllByLabelText("From");
    const [to] = screen.getAllByLabelText("To");
    if (!from || !to) throw new Error("no salary fields");
    await user.type(from, "50000");
    await user.type(to, "60000");
    await user.click(screen.getByRole("button", { name: "Skip this step" }));
    await screen.findByText(/Step 6 of 7/);
    await user.click(screen.getByRole("button", { name: "Skip this step" }));
    await screen.findByText(/Step 7 of 7/);
    const summary = screen.getByRole("region", { name: /Summary/ });
    expect(summary).toHaveTextContent("Not given");
    expect(summary).not.toHaveTextContent("50000");
    expect(summary).toHaveTextContent("No place yet");
    expect(summary).not.toHaveTextContent("Lyon");
    expect(profile.saves.filter((save) => save.section === "search")).toEqual(
      [],
    );
  });

  it("returns to the summary after a Change", async () => {
    fakeSession(member);
    const profile = fakeProfile(null);
    fakeOnboarding(withRoles, SETTINGS, profile);
    const user = userEvent.setup();
    renderApp("/welcome?step=summary");
    await screen.findByText(/Step 7 of 7/);
    await user.click(screen.getByRole("button", { name: "Change Where" }));
    await screen.findByText(/Step 3 of 7/);
    await user.click(screen.getByRole("checkbox", { name: "Anywhere" }));
    await user.click(screen.getByRole("radio", { name: "Remote" }));
    await user.click(next());
    await screen.findByText(/Step 7 of 7/);
    expect(screen.getByRole("region", { name: /Summary/ })).toHaveTextContent(
      "Anywhere; Remote",
    );
  });

  it("never reaches Start from the address while no role is saved", async () => {
    fakeSession(member);
    fakeProfile(null);
    fakeOnboarding({ ...fresh, passed: ["profile"] });
    renderApp("/welcome?step=summary");
    await screen.findByText(/Step 2 of 7/);
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
      screen.queryByRole("heading", { level: 1, name: "Application queue" }),
    ).toBeNull();
    await user.click(
      await screen.findByRole("button", { name: "Skip this step" }),
    );
    await screen.findByText(/Step 2 of 7/);
    expect(app.location()).toContain("next=%2Fapplications%3Ftab%3Dsent");
    await user.click(screen.getByRole("button", { name: "Skip for now" }));
    await waitFor(() => {
      expect(app.location()).toBe("/applications?tab=sent");
    });
  });

  it("starts the reading of a ZIP or a pasted text from step 1", async () => {
    fakeSession(member);
    fakeProfile(null);
    fakeOnboarding();
    fakeDocuments([
      fakeDocument({ id: "d1", name: "linkedin-export.zip" }),
      fakeDocument({ id: "d2", name: "pasted.txt" }),
    ]);
    fakePrompt();
    const runs = fakeRuns({
      consent: {
        version: 1,
        text: CONSENT_TEXT,
        reminder: REMINDER,
        accepted: true,
        accepted_at: "2026-10-01T08:00:00Z",
      },
    });
    const user = userEvent.setup();
    renderApp("/welcome?step=profile");
    await screen.findByText(/Step 1 of 7/);
    // nothing of the run is asked for while the disclosure is closed
    expect(runs.calls).toEqual([]);
    await user.click(screen.getByRole("button", { name: "Advanced" }));
    const read = await screen.findByRole("region", {
      name: "Read them into your profile",
    });
    await user.click(
      await within(read).findByRole("checkbox", { name: /linkedin-export/ }),
    );
    await user.click(
      within(read).getByRole("button", { name: "See the estimate" }),
    );
    const dialog = await screen.findByRole("dialog", {
      name: "Before the run starts",
    });
    await within(dialog).findByTestId("estimate");
    await user.click(
      within(dialog).getByRole("button", { name: "Start the run" }),
    );
    await waitFor(() => {
      expect(runs.sent("POST", "/api/ingestion/runs")).toHaveLength(1);
    });
    expect(runs.sent("POST", "/api/ingestion/runs")[0]?.body).toMatchObject({
      document_ids: ["d1"],
    });
    // the wizard stays on its step, the next steps can be answered meanwhile
    expect(stepHeading()).toHaveTextContent("Step 1 of 7");
    expect(next()).toBeEnabled();
  });

  it("uses no dash as a separator in what it shows", async () => {
    fakeSession(member);
    fakeProfile(null);
    fakeOnboarding({ ...onboardingDone, open_wizard: false });
    for (const step of [
      "looking",
      "where",
      "work",
      "pay",
      "apply",
      "summary",
    ]) {
      const app = renderApp(`/welcome?step=${step}`);
      await screen.findByText(/Step \d of 7/);
      expect(document.body.textContent).not.toMatch(/[–—]| - /);
      app.unmount();
    }
  });
});
