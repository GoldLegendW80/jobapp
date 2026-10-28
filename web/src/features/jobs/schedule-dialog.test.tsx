import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { admin, fakeSession } from "@/test/auth";
import { fakeJobsApi, makeJob } from "@/test/jobs";
import { renderApp } from "@/test/render";
import { emptySweeps, server } from "@/test/server";

import type { ScheduleState } from "./sweeps-api";

const SEARCH = {
  title_include: { terms: ["ai engineer"], advanced_regex: false },
  boards: [{ company: "Acme", ats: "ashby", org: "acme", enabled: true }],
  never_apply: ["No security roles: security"],
};

interface Fake {
  puts: Array<{ base_revision: number; data: Record<string, unknown> }>;
  lists: number;
}

/**
 * GET /api/profile answers `search` at revision 7; PUT /api/profile/search answers each of
 * `answers` in turn (a Response), then saves. GET /api/jobs/sweeps answers `schedule`.
 */
function fakeProfile(
  options: {
    search?: Record<string, unknown> | null;
    answers?: Response[];
    schedule?: Partial<ScheduleState>;
  } = {},
): Fake {
  const state: Fake = { puts: [], lists: 0 };
  const answers = [...(options.answers ?? [])];
  let schedule: ScheduleState = {
    ...emptySweeps.schedule,
    ...options.schedule,
  };
  server.use(
    http.get("*/api/profile", () =>
      HttpResponse.json({
        revision: 7,
        sections: {
          search: options.search === undefined ? SEARCH : options.search,
        },
      }),
    ),
    http.put("*/api/profile/search", async ({ request }) => {
      const body = (await request.json()) as Fake["puts"][number];
      state.puts.push(body);
      const answer = answers.shift();
      if (answer) return answer;
      const saved = body.data.sweep_schedule as ScheduleState;
      schedule = {
        ...saved,
        active: saved.enabled,
        next_run: saved.enabled ? "2026-10-10T04:17:00Z" : null,
      };
      return HttpResponse.json({
        revision: body.base_revision + 1,
        sections: { search: body.data },
      });
    }),
    http.get("*/api/jobs/sweeps", () => {
      state.lists += 1;
      return HttpResponse.json({ items: [], schedule });
    }),
  );
  return state;
}

beforeEach(() => {
  fakeSession(admin);
  vi.spyOn(window, "scrollTo").mockImplementation(() => {});
});

async function openSchedule() {
  fakeJobsApi([makeJob(1)]);
  renderApp("/jobs");
  await screen.findByRole("table", { name: "Postings" });
  const user = userEvent.setup();
  const button = await screen.findByRole("button", { name: /^Schedule/ });
  await waitFor(() => {
    expect(button).toHaveTextContent(/Off|On/);
  });
  await user.click(button);
  const dialog = await screen.findByRole("dialog", {
    name: "Automatic search",
  });
  return { user, dialog, button };
}

describe("Schedule", () => {
  it("is off by default; turning it on saves the schedule into the search section", async () => {
    const fake = fakeProfile();
    const { user, dialog, button } = await openSchedule();
    expect(button).toHaveTextContent("Off");
    const toggle = within(dialog).getByRole("checkbox", {
      name: "Search automatically",
    });
    expect(toggle).not.toBeChecked();
    expect(within(dialog).getByRole("status")).toHaveTextContent(
      "Automatic search is off.",
    );
    await user.click(toggle);
    expect(within(dialog).getByRole("status")).toHaveTextContent(
      /^Next automatic search: \w{3} \d{1,2} \w{3}, \d\d:\d\d \(Europe\/Paris\)\.$/,
    );
    // Weekdays only, and a second time in the evening.
    await user.click(within(dialog).getByRole("button", { name: "Saturday" }));
    await user.click(within(dialog).getByRole("button", { name: "Sunday" }));
    expect(
      within(dialog).getByRole("button", { name: "Sunday" }),
    ).toHaveAttribute("aria-pressed", "false");
    await user.click(
      within(dialog).getByRole("button", { name: "Add a time" }),
    );
    const second = within(dialog).getByLabelText("Time 2");
    await user.clear(second);
    await user.type(second, "18:30");
    await user.selectOptions(
      within(dialog).getByLabelText("Time zone"),
      "America/New_York",
    );
    await user.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => {
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    });
    expect(fake.puts).toHaveLength(1);
    expect(fake.puts[0]).toEqual({
      base_revision: 7,
      data: {
        ...SEARCH,
        sweep_schedule: {
          enabled: true,
          days: ["monday", "tuesday", "wednesday", "thursday", "friday"],
          times: ["06:17", "18:30"],
          timezone: "America/New_York",
        },
      },
    });
    expect(
      await screen.findByText(
        "Automatic search: on, weekdays at 06:17 and 18:30 (America/New_York).",
      ),
    ).toBeInTheDocument();
    await waitFor(() => {
      expect(button).toHaveTextContent("On");
    });
    expect(button).toHaveAttribute(
      "title",
      "Automatic search: on, weekdays at 06:17 and 18:30 (America/New_York). Next: Sat 10 Oct, 00:17 (America/New_York).",
    );
  });

  it("puts the schedule onto a profile changed meanwhile (409), once", async () => {
    const newer = { ...SEARCH, never_apply: ["Changed in another tab: x"] };
    const fake = fakeProfile({
      answers: [
        HttpResponse.json(
          {
            detail: "The profile changed since you loaded it.",
            revision: 9,
            sections: { search: newer },
          },
          { status: 409 },
        ),
      ],
    });
    const { user, dialog } = await openSchedule();
    await user.click(
      within(dialog).getByRole("checkbox", { name: "Search automatically" }),
    );
    await user.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => {
      expect(fake.puts).toHaveLength(2);
    });
    expect(fake.puts[1]!.base_revision).toBe(9);
    expect(fake.puts[1]!.data.never_apply).toEqual([
      "Changed in another tab: x",
    ]);
    expect(
      (fake.puts[1]!.data.sweep_schedule as { enabled: boolean }).enabled,
    ).toBe(true);
    await waitFor(() => {
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    });
  });

  it("reports a second conflict and a refused schedule", async () => {
    const conflict = () =>
      HttpResponse.json(
        { detail: "changed", revision: 9, sections: { search: SEARCH } },
        { status: 409 },
      );
    fakeProfile({
      answers: [
        conflict(),
        conflict(),
        HttpResponse.json(
          { detail: "data.sweep_schedule.timezone: unknown time zone" },
          { status: 422 },
        ),
      ],
    });
    const { user, dialog } = await openSchedule();
    await user.click(
      within(dialog).getByRole("checkbox", { name: "Search automatically" }),
    );
    await user.click(screen.getByRole("button", { name: "Save" }));
    expect(await within(dialog).findByRole("alert")).toHaveTextContent(
      "Your profile changed in another tab twice in a row.",
    );
    await user.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => {
      expect(within(dialog).getByRole("alert")).toHaveTextContent(
        "Not saved: data.sweep_schedule.timezone: unknown time zone.",
      );
    });
  });

  it("needs a day and a complete time while on", async () => {
    fakeProfile();
    const { user, dialog } = await openSchedule();
    await user.click(
      within(dialog).getByRole("checkbox", { name: "Search automatically" }),
    );
    for (const day of [
      "Monday",
      "Tuesday",
      "Wednesday",
      "Thursday",
      "Friday",
      "Saturday",
      "Sunday",
    ])
      await user.click(within(dialog).getByRole("button", { name: day }));
    expect(within(dialog).getByText("Pick at least one day.")).toBeVisible();
    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
    await user.click(within(dialog).getByRole("button", { name: "Monday" }));
    await user.clear(within(dialog).getByLabelText("Time 1"));
    expect(
      within(dialog).getByText("Every time needs hours and minutes."),
    ).toBeVisible();
    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
    // At most four times.
    await user.type(within(dialog).getByLabelText("Time 1"), "07:00");
    for (let i = 0; i < 3; i += 1)
      await user.click(
        within(dialog).getByRole("button", { name: "Add a time" }),
      );
    expect(
      within(dialog).queryByRole("button", { name: "Add a time" }),
    ).not.toBeInTheDocument();
    await user.click(
      within(dialog).getByRole("button", { name: "Remove time 4" }),
    );
    expect(within(dialog).getAllByLabelText(/^Time \d$/)).toHaveLength(3);
  });

  it("turns it off, and works on a profile that has no search section yet", async () => {
    const fake = fakeProfile({
      search: null,
      schedule: { enabled: true, active: false, next_run: null },
    });
    const { user, dialog, button } = await openSchedule();
    expect(button).toHaveTextContent("On");
    expect(button).toHaveAttribute(
      "title",
      "Automatic search: on, every day at 06:17 (Europe/Paris). No job website is ticked in Find jobs, so nothing runs.",
    );
    await user.click(
      within(dialog).getByRole("checkbox", { name: "Search automatically" }),
    );
    await user.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => {
      expect(fake.puts).toHaveLength(1);
    });
    expect(fake.puts[0]!.data).toEqual({
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
    });
    expect(
      await screen.findByText("Automatic search: off."),
    ).toBeInTheDocument();
  });

  it("opens from the sweep history too", async () => {
    fakeProfile({
      schedule: {
        enabled: true,
        active: true,
        next_run: "2026-10-10T04:17:00Z",
      },
    });
    fakeJobsApi([makeJob(1)]);
    renderApp("/jobs");
    const user = userEvent.setup();
    await user.click(
      await screen.findByRole("button", { name: "Sweep history" }),
    );
    const history = await screen.findByRole("dialog", { name: "Sweeps" });
    await user.click(
      await within(history).findByRole("button", { name: "Change schedule" }),
    );
    const dialog = await screen.findByRole("dialog", {
      name: "Automatic search",
    });
    expect(
      within(dialog).getByRole("checkbox", { name: "Search automatically" }),
    ).toBeChecked();
  });
});
