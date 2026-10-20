import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { afterEach, describe, expect, it } from "vitest";

import { fakeDeck } from "@/features/opportunities/deck/deck-test-api";
import { fakeSession, member } from "@/test/auth";
import { EMPTY, profileKey } from "@/features/profile/api";
import { fakeProfile, type FakeProfile } from "@/test/profile";
import { renderApp } from "@/test/render";
import { onboardingDone, server } from "@/test/server";

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
  company_days: 30,
  show_postings_from: null,
  write_documents_from: null,
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
 * The wizard's routes. With `profile`, `done` follows its saves and a step passed is one
 * revision of its preferences, as on the api.
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
          profile.revision += 1;
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
        sweep: "no_boards",
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
    // each step passed is kept in the profile (one revision): the next save starts after it
    expect(calls.steps).toEqual(["looking", "where"]);
    expect(profile.saves[1]?.body.base_revision).toBe(2);

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
    expect(screen.getByText(/covers 0 boards/)).toBeInTheDocument();
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
      screen.queryByRole("heading", { level: 1, name: "Applications" }),
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
