import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { toast } from "sonner";
import { afterEach, describe, expect, it } from "vitest";

import { WORDING } from "@/lib/wording";
import { admin, fakeSession } from "@/test/auth";
import { fakeProfile, haraldLike } from "@/test/profile";
import { renderApp } from "@/test/render";
import { server } from "@/test/server";

import type { Search, Weekday } from "./api";

afterEach(() => {
  toast.dismiss();
});

async function openSearch(
  initial = haraldLike() as Parameters<typeof fakeProfile>[0],
) {
  fakeSession(admin);
  const state = fakeProfile(initial);
  const user = userEvent.setup();
  const app = renderApp("/profile?tab=search");
  await screen.findByRole("form", { name: "Search" });
  return { user, state, ...app };
}

const save = () => screen.getByRole("button", { name: "Save search" });
const saved = (state: ReturnType<typeof fakeProfile>, index = 0) =>
  state.saves[index]!.body.data as Search;
describe("search tab: terms and preferences", () => {
  it("shows the terms, preferences and rules", async () => {
    await openSearch();
    const include = screen.getByRole("list", {
      name: "Terms of Job titles",
    });
    expect(include).toHaveTextContent("applied ai");
    // Saved regular expressions keep working and say so; there is no switch (owner, 2026-10-28).
    expect(
      screen.getAllByText(WORDING.listUsesRegularExpressions).length,
    ).toBeGreaterThan(0);
    expect(
      screen.queryByRole("switch", { name: /regular expressions/i }),
    ).toBeNull();
    expect(
      screen.getByRole("list", { name: "Terms of Places" }),
    ).toHaveTextContent("Paris");
    expect(
      screen.getByRole("list", { name: "Terms of Unwanted places" }),
    ).toHaveTextContent("United States");
    expect(
      screen.getByRole("button", { name: "CDI (permanent)" }),
    ).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: "VIE" })).toHaveAttribute(
      "aria-pressed",
      "false",
    );
    expect(screen.getByLabelText("Remote stance")).toHaveValue("any");
    expect(screen.getByLabelText("Minimum fit")).toHaveValue("70");
    expect(screen.getByLabelText("Posting age window (days)")).toHaveValue(
      "30",
    );
    expect(screen.getByLabelText("Rule 1")).toHaveValue(
      "No cybersecurity roles.",
    );
  });

  it("edits terms, contract types, stance, fit and days, then saves", async () => {
    const { user, state } = await openSearch();
    await user.type(
      screen.getByLabelText("New term for Unwanted titles"),
      "\\bintern{enter}",
    );
    await user.type(
      screen.getByLabelText("New term for Places"),
      "Lyon{enter}",
    );
    await user.type(
      screen.getByLabelText("New term for Unwanted places"),
      "Canada{enter}",
    );
    await user.click(screen.getByRole("button", { name: "Remove Remote" }));
    await user.click(screen.getByRole("button", { name: "VIE" }));
    await user.click(screen.getByRole("button", { name: "Freelance" }));
    await user.click(screen.getByRole("button", { name: "CDD (fixed term)" }));
    expect(screen.getByRole("button", { name: "VIE" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    await user.selectOptions(screen.getByLabelText("Remote stance"), "hybrid");
    fireEvent.change(screen.getByLabelText("Minimum fit"), {
      target: { value: "82" },
    });
    expect(screen.getByText("82 / 100")).toBeVisible();
    const days = screen.getByLabelText("Posting age window (days)");
    await user.clear(days);
    await user.type(days, "14");
    await user.click(save());
    expect(await screen.findByText("Search saved.")).toBeVisible();
    const data = saved(state);
    expect(data.title_exclude).toEqual({
      advanced_regex: true,
      terms: ["\\bcyber\\b", "\\bintern"],
    });
    expect(data.location.terms).toEqual(["Paris", "Lyon"]);
    expect(data.location_exclude).toEqual({
      advanced_regex: false,
      terms: ["United States", "Canada"],
    });
    // Always in the canonical order, whatever the order of the clicks.
    expect(data.contract_types).toEqual(["CDI", "CDD", "VIE"]);
    expect(data.remote_stance).toBe("hybrid");
    expect(data.min_fit).toBe(82);
    expect(data.days).toBe(14);
    expect(data.never_apply).toEqual(haraldLike().search.never_apply);
  });

  it("refuses a days window outside 1 to 365 and accepts no stance", async () => {
    const { user, state } = await openSearch();
    const days = screen.getByLabelText("Posting age window (days)");
    for (const bad of ["0", "366", "7.5", "abc", " "]) {
      await user.clear(days);
      await user.type(days, bad);
      await user.click(save());
      expect(
        await screen.findByText("Enter a number of days between 1 and 365."),
      ).toBeVisible();
    }
    expect(state.saves).toHaveLength(0);
    await user.clear(days);
    await user.type(days, "365");
    await user.selectOptions(screen.getByLabelText("Remote stance"), "");
    await user.click(save());
    await screen.findByText("Search saved.");
    expect(saved(state).remote_stance).toBeNull();
    expect(saved(state).days).toBe(365);
  });

  it("puts the server's regex error under the right title chip", async () => {
    const { user } = await openSearch();
    server.use(
      http.put("*/api/profile/search", () =>
        HttpResponse.json(
          {
            detail:
              "data.title_include.terms: term 2: invalid regular expression at position 0: nothing to repeat",
          },
          { status: 422 },
        ),
      ),
    );
    await user.click(screen.getByRole("button", { name: "VIE" }));
    await user.click(save());
    const list = screen.getByRole("list", {
      name: "Terms of Job titles",
    });
    const llm = within(list).getAllByRole("listitem")[1]!;
    expect(
      await within(llm).findByText(
        "Invalid regular expression at position 0: nothing to repeat.",
      ),
    ).toBeVisible();
  });

  it("adds, edits and removes never-apply rules", async () => {
    const { user, state } = await openSearch();
    await user.click(screen.getByRole("button", { name: "Add rule" }));
    await user.click(save());
    expect(await screen.findByText("Enter a rule.")).toBeVisible();
    await user.type(screen.getByLabelText("Rule 2"), "  No defence roles. ");
    await user.click(screen.getByRole("button", { name: "Remove rule 1" }));
    await user.click(save());
    await screen.findByText("Search saved.");
    expect(saved(state).never_apply).toEqual(["No defence roles."]);
  });

  it("maps the server's error on a rule to its field", async () => {
    const { user } = await openSearch();
    server.use(
      http.put("*/api/profile/search", () =>
        HttpResponse.json(
          { detail: "data.never_apply.0: control characters are not allowed" },
          { status: 422 },
        ),
      ),
    );
    await user.type(screen.getByLabelText("Rule 1"), "!");
    await user.click(save());
    expect(
      await screen.findByText("Control characters are not allowed."),
    ).toBeVisible();
    expect(screen.getByLabelText("Rule 1")).toHaveAttribute(
      "aria-invalid",
      "true",
    );
  });

  it("starts empty for a new user", async () => {
    const { user, state } = await openSearch(null);
    expect(screen.getByText("No rule yet.")).toBeVisible();
    // The four lists (job titles, unwanted titles, places, unwanted places), and the industries
    // kept mounted under Advanced.
    expect(screen.getAllByText("No term yet.")).toHaveLength(5);
    expect(screen.getByLabelText("Minimum fit")).toHaveValue("70");
    await user.type(
      screen.getByLabelText("New term for Job titles"),
      "ai engineer{enter}",
    );
    await user.click(save());
    await screen.findByText("Search saved.");
    expect(saved(state)).toEqual({
      title_include: { advanced_regex: false, terms: ["ai engineer"] },
      title_exclude: { advanced_regex: false, terms: [] },
      location: { advanced_regex: false, terms: [] },
      location_exclude: { advanced_regex: false, terms: [] },
      contract_types: [],
      remote_stance: null,
      min_fit: 70,
      days: 30,
      never_apply: [],
      sweep_schedule: {
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
      },
      documents: { auto: true, min_fit: 70, daily_ceiling: 100 },
      seniority: [],
      industries: { advanced_regex: false, terms: [] },
      company_types: [],
      compensation: { salary: null, daily_rate: null },
    });
  });
});

describe("search tab: automatic search", () => {
  const line = () => screen.getByText("Automatic search:").parentElement;
  const ON = {
    enabled: true,
    days: ["monday", "friday"] as Weekday[],
    times: ["18:30", "06:17"],
    timezone: "America/New_York",
  };

  it("shows off for a section saved before the schedule existed, with a link to Jobs", async () => {
    const legacy = haraldLike();
    const search: Partial<Search> = { ...legacy.search };
    delete search.sweep_schedule;
    await openSearch({ ...legacy, search });
    expect(line()).toHaveTextContent("Automatic search: off");
    expect(
      screen.getByRole("link", { name: "Change in Jobs" }),
    ).toHaveAttribute("href", "/jobs");
  });

  it("describes a schedule that is on", async () => {
    const data = haraldLike();
    data.search.sweep_schedule = ON;
    await openSearch(data);
    expect(line()).toHaveTextContent(
      "Automatic search: on, Monday and Friday at 06:17 and 18:30 (America/New_York)",
    );
  });

  it("keeps the schedule unchanged when the tab is saved", async () => {
    const data = haraldLike();
    data.search.sweep_schedule = ON;
    const { user, state } = await openSearch(data);
    await user.clear(screen.getByLabelText("Posting age window (days)"));
    await user.type(screen.getByLabelText("Posting age window (days)"), "14");
    await user.click(save());
    await screen.findByText("Search saved.");
    expect(saved(state).sweep_schedule).toEqual(ON);
    expect(saved(state).days).toBe(14);
    // And again after the form was reset from the saved version.
    await user.click(screen.getByRole("button", { name: "CDD (fixed term)" }));
    await user.click(save());
    await waitFor(() => {
      expect(state.saves).toHaveLength(2);
    });
    expect(saved(state, 1).sweep_schedule).toEqual(ON);
  });

  it("saves the default off schedule for a section that had none", async () => {
    const legacy = haraldLike();
    const search: Partial<Search> = { ...legacy.search };
    delete search.sweep_schedule;
    const { user, state } = await openSearch({ ...legacy, search });
    await user.click(screen.getByRole("button", { name: "VIE" }));
    await user.click(save());
    await screen.findByText("Search saved.");
    expect(saved(state).sweep_schedule.enabled).toBe(false);
  });
});

// Owner, 2026-10-28: searches run the job websites ticked in Find jobs alone. The company
// boards, the starter list, the fixed browser readers and the freelance feeds are deleted: a
// profile read before shows none of them here and a save sends none back. LinkedIn and Free-Work
// rows stay, their permission set in Find jobs.
describe("search tab: sources no search reads", () => {
  const stored = {
    boards: [
      {
        company: "Example Labs",
        ats: "ashby" as const,
        org: "example-labs",
        enabled: true,
        extra_exclude: { advanced_regex: false, terms: ["intern"] },
      },
    ],
    browser_sources: [
      {
        kind: "google_careers" as const,
        query: "ai engineer",
        location: null,
        enabled: true,
      },
      {
        kind: "linkedin" as const,
        query: "ai engineer",
        location: null,
        enabled: true,
        accepted_at: null,
        text_version: null,
        interactive_only: false,
      },
    ],
    feeds: [
      {
        kind: "remoteok" as const,
        terms: { advanced_regex: false, terms: ["agents"] },
        enabled: true,
      },
    ],
  };

  it("shows no boards table, starter list, browser sites card nor feeds card", async () => {
    await openSearch({
      ...haraldLike(),
      search: { ...haraldLike().search, ...stored } as unknown as Search,
    });
    expect(screen.queryByRole("table", { name: "Boards" })).toBeNull();
    expect(screen.queryByRole("region", { name: "Add company" })).toBeNull();
    expect(screen.queryByText(/starter/i)).toBeNull();
    expect(
      screen.queryByRole("list", { name: "Job sites in your browser" }),
    ).toBeNull();
    expect(screen.queryByRole("button", { name: "Add site" })).toBeNull();
    expect(screen.queryByText("Freelance and project sources")).toBeNull();
    expect(
      screen.getByText("Searches run the job websites ticked in Find jobs."),
    ).toBeVisible();
  });

  it("sends no boards, feeds nor fixed readers, and keeps the LinkedIn row", async () => {
    const { user, state } = await openSearch({
      ...haraldLike(),
      search: { ...haraldLike().search, ...stored } as unknown as Search,
    });
    const days = screen.getByLabelText("Posting age window (days)");
    await user.clear(days);
    await user.type(days, "14");
    await user.click(save());
    await screen.findByText("Search saved.");
    const body = saved(state);
    expect(body.days).toBe(14);
    expect(body).not.toHaveProperty("boards");
    expect(body).not.toHaveProperty("feeds");
    // The fields the form carries (the api keeps LinkedIn's acceptance fields of its own).
    expect(body.browser_sources).toEqual([
      {
        kind: "linkedin",
        query: "ai engineer",
        location: null,
        enabled: true,
      },
    ]);
  });
});
