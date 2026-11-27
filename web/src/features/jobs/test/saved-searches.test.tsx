import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { EMPTY } from "@/features/profile/api/profile-api";
import type { Sweep } from "@/features/jobs/api/sweeps-api";
import {
  copyName,
  criteriaLine,
  halfSavedWords,
  searchOf,
  nameProblem,
  runWords,
} from "@/features/jobs/helpers/saved-searches";
import { admin, fakeSession } from "@/test/auth";
import { fakeJobsApi, makeJob } from "@/test/jobs";
import { fakeProfile } from "@/test/profile";
import { renderApp } from "@/test/render";
import { fakeSearches, makeSearch } from "@/test/searches";
import { server } from "@/test/server";

const REMOTE = "00000000-0000-4000-8000-000000000001";
const LYON = "00000000-0000-4000-8000-000000000002";
const OLD_RUN = "0b6c3a52-6d1e-4c4f-9f43-2a41c1a1e0aa";
const TERMS = (terms: string[]) => ({ advanced_regex: false, terms });

function sweep(
  id: string,
  search: Sweep["search"],
  over: Partial<Sweep> = {},
): Sweep {
  return {
    id,
    trigger: "manual",
    status: "done",
    started_at: "2026-11-25T08:00:00Z",
    finished_at: "2026-11-25T08:10:00Z",
    error: null,
    boards_total: 0,
    boards_done: 0,
    boards_ok: 0,
    boards_failed: [],
    current_board: null,
    seen: 9,
    matched: 4,
    new: 3,
    duplicates: 0,
    skipped_by_rule: 0,
    duration: 600,
    sources: [],
    search,
    ...over,
  };
}

beforeEach(() => {
  fakeSession(admin);
  vi.spyOn(window, "scrollTo").mockImplementation(() => {});
  fakeProfile({ search: structuredClone(EMPTY.search) }, 6);
  fakeJobsApi([makeJob(1)]);
  server.use(
    http.get("*/api/browser/sites", () => HttpResponse.json({ sites: [] })),
  );
});

async function openFindJobs() {
  const app = renderApp("/jobs");
  await screen.findByRole("list", { name: "Postings" });
  const user = userEvent.setup();
  await user.click(screen.getByRole("button", { name: "Find jobs" }));
  const list = await screen.findByRole("list", { name: "Your searches" });
  return { user, list, app };
}

function rowOf(list: HTMLElement, name: string) {
  return within(list).getByRole("listitem", { name });
}

describe("Your searches (jobs J33)", () => {
  it("lists the main search first, then each saved one with what it searches, its schedule and last run", async () => {
    fakeSearches({
      main: () => ({ title_include: TERMS(["Data engineer"]) }),
      saved: [
        makeSearch(
          REMOTE,
          "Remote roles",
          {
            title_include: TERMS(["ML engineer"]),
            location: TERMS(["Lyon"]),
            web_search: false,
            interactive_sites: [
              {
                platform: "indeed",
                url: "https://www.indeed.com/",
                enabled: true,
                mode: "llm_browser",
              },
            ],
          },
          {
            last_run: {
              sweep_id: OLD_RUN,
              status: "done",
              started_at: "2026-09-02T08:00:00Z",
              finished_at: "2026-09-02T08:10:00Z",
              new: 3,
            },
          },
        ),
      ],
    });
    const { list, user, app } = await openFindJobs();
    const rows = within(list).getAllByRole("listitem");
    expect(
      rows.map((row) => within(row).getByRole("heading").textContent),
    ).toEqual(["Main search", "Remote roles"]);
    const remote = rowOf(list, "Remote roles");
    expect(remote).toHaveTextContent("ML engineer; Lyon; on Indeed");
    expect(remote).toHaveTextContent("Runs when you start it.");
    // Its last run is the api's, older than the last 20 sweeps (none listed here).
    expect(remote).toHaveTextContent(/Last run .*, 3 new postings\./);
    expect(rowOf(list, "Main search")).toHaveTextContent("Not run yet.");
    expect(
      screen.getByRole("link", { name: "Settings, Search" }),
    ).toHaveAttribute("href", "/settings/search");
    // "3 new postings" opens the list on that run's postings, and Find jobs closes.
    await user.click(
      within(remote).getByRole("link", { name: "3 new postings" }),
    );
    expect(app.location()).toBe(`/jobs?sweep=${OLD_RUN}`);
    await waitFor(() => {
      expect(screen.queryByRole("dialog", { name: "Find jobs" })).toBeNull();
    });
  });

  it("starts the main search in two clicks, Find jobs then Run now, and follows it", async () => {
    const searches = fakeSearches({
      started: (id, name) =>
        sweep("run-1", { id, name }, { status: "running", finished_at: null }),
    });
    server.use(
      http.get("*/api/jobs/sweeps/run-1", () =>
        HttpResponse.json({
          ...sweep("run-1", { id: "main", name: "Main search" }),
          new_jobs: [],
        }),
      ),
    );
    const { user, list } = await openFindJobs();
    await user.click(
      within(list).getByRole("button", { name: "Run Main search now" }),
    );
    expect(searches.runs).toEqual(["main"]);
    await waitFor(() => {
      expect(screen.queryByRole("list", { name: "Your searches" })).toBeNull();
    });
  });

  it("keeps a run asked while another runs in the line, with its place, and takes it back", async () => {
    const searches = fakeSearches({
      saved: [makeSearch(REMOTE, "Remote roles")],
    });
    searches.running = "busy";
    const { user, list } = await openFindJobs();
    await user.click(
      within(list).getByRole("button", { name: "Run Remote roles now" }),
    );
    const row = rowOf(list, "Remote roles");
    await within(row).findByText(/Waits in line: number 1/);
    expect(
      await screen.findByText("Remote roles waits in line: number 1."),
    ).toBeInTheDocument();
    expect(row).toHaveTextContent("Starts when the running search ends.");
    expect(
      within(row).getByRole("button", { name: "Run Remote roles now" }),
    ).toBeDisabled();
    await user.click(within(row).getByRole("button", { name: "Take back" }));
    await waitFor(() => {
      expect(within(row).queryByText(/Waits in line/)).toBeNull();
    });
    expect(searches.line).toEqual([]);
  });

  it("puts a saved search's Save and start in the line while another runs, in Run now's words", async () => {
    const searches = fakeSearches({
      saved: [makeSearch(REMOTE, "Lyon")],
    });
    searches.running = "busy";
    const { user, list } = await openFindJobs();
    await user.click(within(list).getByRole("button", { name: "Edit Lyon" }));
    const dialog = await screen.findByRole("dialog", { name: "Find jobs" });
    await user.click(
      await within(dialog).findByRole("button", {
        name: "Save and start search",
      }),
    );
    expect(
      await screen.findByText("Lyon waits in line: number 1."),
    ).toBeInTheDocument();
    expect(searches.writes.map((one) => one.method)).toEqual(["PUT"]);
    expect(searches.runs).toEqual([REMOTE]);
    const row = rowOf(
      await screen.findByRole("list", { name: "Your searches" }),
      "Lyon",
    );
    await within(row).findByText(/Waits in line: number 1/);
  });

  it("duplicates, renames and deletes a saved search, and offers neither rename nor delete for the main one", async () => {
    const searches = fakeSearches({
      saved: [makeSearch(REMOTE, "Remote roles")],
    });
    const { user, list } = await openFindJobs();
    await user.click(
      within(list).getByRole("button", {
        name: "More actions for Main search",
      }),
    );
    expect(screen.getByRole("menuitem", { name: "Duplicate" })).toBeVisible();
    expect(screen.queryByRole("menuitem", { name: "Rename" })).toBeNull();
    expect(screen.queryByRole("menuitem", { name: "Delete" })).toBeNull();
    await user.keyboard("{Escape}");

    await user.click(
      within(list).getByRole("button", {
        name: "More actions for Remote roles",
      }),
    );
    await user.click(screen.getByRole("menuitem", { name: "Duplicate" }));
    await within(list).findByRole("heading", { name: "Remote roles copy" });

    await user.click(
      within(list).getByRole("button", {
        name: "More actions for Remote roles",
      }),
    );
    await user.click(screen.getByRole("menuitem", { name: "Rename" }));
    const name = within(list).getByRole("textbox", { name: "New name" });
    await user.clear(name);
    await user.type(name, "remote ROLES copy");
    await user.click(within(list).getByRole("button", { name: "Save name" }));
    expect(await within(list).findByRole("alert")).toHaveTextContent(
      "You already have a search with this name.",
    );
    await user.clear(name);
    await user.type(name, "Remote in Lyon");
    await user.click(within(list).getByRole("button", { name: "Save name" }));
    await within(list).findByRole("heading", { name: "Remote in Lyon" });
    expect(searches.writes.at(-1)).toMatchObject({
      method: "PUT",
      id: REMOTE,
      body: { name: "Remote in Lyon", revision: 1 },
    });

    await user.click(
      within(list).getByRole("button", {
        name: "More actions for Remote in Lyon",
      }),
    );
    await user.click(screen.getByRole("menuitem", { name: "Delete" }));
    const confirm = await screen.findByRole("alertdialog", {
      name: "Delete Remote in Lyon?",
    });
    await user.click(within(confirm).getByRole("button", { name: "Delete" }));
    await waitFor(() => {
      expect(
        within(list).queryByRole("heading", { name: "Remote in Lyon" }),
      ).toBeNull();
    });
    expect(searches.saved.map((one) => one.name)).toEqual([
      "Remote roles copy",
    ]);
  });

  it("makes a new search with only the keys of one search, and keeps each search's own draft", async () => {
    const searches = fakeSearches({
      saved: [makeSearch(REMOTE, "Remote roles")],
    });
    const { user } = await openFindJobs();
    await user.click(screen.getByRole("button", { name: "Edit Remote roles" }));
    let dialog = await screen.findByRole("dialog", { name: "Find jobs" });
    await user.type(await within(dialog).findByLabelText("Name"), " soon");
    await user.click(within(dialog).getByRole("button", { name: "Back" }));
    await user.click(
      await screen.findByRole("button", { name: "Edit Remote roles" }),
    );
    dialog = await screen.findByRole("dialog", { name: "Find jobs" });
    expect(await within(dialog).findByLabelText("Name")).toHaveValue(
      "Remote roles soon",
    );
    await user.click(within(dialog).getByRole("button", { name: "Back" }));

    await user.click(await screen.findByRole("button", { name: "New search" }));
    dialog = await screen.findByRole("dialog", { name: "Find jobs" });
    const name = await within(dialog).findByLabelText("Name");
    expect(name).toHaveValue("");
    await user.click(within(dialog).getByRole("button", { name: "Save" }));
    expect(await within(dialog).findByRole("alert")).toHaveTextContent(
      "Give this search a name.",
    );
    await user.type(name, "Main search");
    await user.click(within(dialog).getByRole("button", { name: "Save" }));
    expect(await within(dialog).findByRole("alert")).toHaveTextContent(
      "Main search is the name of your main search.",
    );
    await user.clear(name);
    await user.type(name, "Robotics");
    await user.click(within(dialog).getByRole("button", { name: "Save" }));
    await screen.findByRole("heading", { name: "Robotics" });
    const body = searches.writes.at(-1)?.body as {
      criteria: Record<string, unknown>;
    };
    expect(Object.keys(body.criteria)).not.toContain("never_apply");
    expect(Object.keys(body.criteria)).not.toContain("compensation");
    expect(body.criteria.web_search).toBe(true);
  });
});

const WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday"];

async function openSchedule(
  user: ReturnType<typeof userEvent.setup>,
  list: HTMLElement,
  name: string,
) {
  await user.click(
    within(list).getByRole("button", { name: `More actions for ${name}` }),
  );
  await user.click(screen.getByRole("menuitem", { name: "Schedule" }));
}

/** Turns the schedule on, on weekdays only, and saves. */
async function weekdaysOnly(
  user: ReturnType<typeof userEvent.setup>,
  dialog: HTMLElement,
) {
  await user.click(
    within(dialog).getByRole("checkbox", { name: "Search automatically" }),
  );
  await user.click(within(dialog).getByRole("button", { name: "Saturday" }));
  await user.click(within(dialog).getByRole("button", { name: "Sunday" }));
  await user.click(within(dialog).getByRole("button", { name: "Save" }));
}

describe("The schedule of each search (jobs J33b)", () => {
  it("schedules a saved search from its row, on the revision it showed, and the row follows without a reload", async () => {
    const criteria = { title_include: TERMS(["ML engineer"]) };
    const searches = fakeSearches({
      saved: [makeSearch(REMOTE, "Remote roles", criteria, { revision: 3 })],
    });
    const { user, list } = await openFindJobs();
    expect(rowOf(list, "Remote roles")).toHaveTextContent(
      "Runs when you start it.",
    );
    await openSchedule(user, list, "Remote roles");
    const dialog = await screen.findByRole("dialog", {
      name: "Automatic search of Remote roles",
    });
    // The time zone is the person's, for every search: named, with where it changes.
    expect(dialog).toHaveTextContent(
      "The times are in your time zone, Europe/Paris, which all your searches share: change it in the schedule of Main search.",
    );
    expect(dialog).toHaveTextContent(
      "When two of your searches are due at the same time, the second waits in line and starts when the first ends.",
    );
    expect(within(dialog).queryByLabelText("Time zone")).toBeNull();
    await weekdaysOnly(user, dialog);

    expect(
      await screen.findByText(
        "Automatic search of Remote roles: on, weekdays at 06:17 (Europe/Paris).",
      ),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("dialog", { name: /^Automatic search/ }),
    ).toBeNull();
    expect(searches.writes).toEqual([
      {
        method: "PUT",
        id: REMOTE,
        body: {
          name: "Remote roles",
          criteria,
          schedule: { enabled: true, days: WEEKDAYS, times: ["06:17"] },
          revision: 3,
        },
      },
    ]);
    await waitFor(() => {
      expect(rowOf(list, "Remote roles")).toHaveTextContent(
        "Automatic: weekdays at 06:17 (Europe/Paris)",
      );
    });
  });

  it("says when the search changed in another tab, then saves on its new revision", async () => {
    const searches = fakeSearches({
      saved: [makeSearch(REMOTE, "Remote roles", {}, { revision: 3 })],
    });
    const { user, list } = await openFindJobs();
    await openSchedule(user, list, "Remote roles");
    const dialog = await screen.findByRole("dialog", {
      name: "Automatic search of Remote roles",
    });
    searches.saved[0]!.revision = 4;
    await weekdaysOnly(user, dialog);
    expect(await within(dialog).findByRole("alert")).toHaveTextContent(
      "This search changed in another tab. Try again.",
    );
    // The choice is kept; the search was read again, so Save goes on its stored revision.
    expect(
      within(dialog).getByRole("button", { name: "Saturday" }),
    ).toHaveAttribute("aria-pressed", "false");
    const save = within(dialog).getByRole("button", { name: "Save" });
    await waitFor(() => {
      expect(save).toBeEnabled();
    });
    await user.click(save);
    await waitFor(() => {
      expect(
        searches.writes.map(
          (one) => (one.body as { revision: number }).revision,
        ),
      ).toEqual([3, 4]);
    });
    await waitFor(() => {
      expect(
        screen.queryByRole("dialog", { name: /^Automatic search/ }),
      ).toBeNull();
    });
  });

  it("says a search deleted meanwhile no longer exists, and a refused save in the api's words", async () => {
    const searches = fakeSearches({
      saved: [makeSearch(REMOTE, "Remote roles"), makeSearch(LYON, "Lyon")],
    });
    const { user, list } = await openFindJobs();
    await openSchedule(user, list, "Remote roles");
    let dialog = await screen.findByRole("dialog", {
      name: "Automatic search of Remote roles",
    });
    searches.saved = searches.saved.filter((one) => one.id !== REMOTE);
    await weekdaysOnly(user, dialog);
    expect(await within(dialog).findByRole("alert")).toHaveTextContent(
      "This search no longer exists.",
    );
    // The row leaves the list; the dialog stays with what holds, and nothing is saved again.
    await waitFor(() => {
      expect(
        within(list).queryByRole("listitem", { name: "Remote roles" }),
      ).toBeNull();
    });
    expect(within(dialog).getByRole("button", { name: "Save" })).toBeDisabled();
    await user.click(within(dialog).getByRole("button", { name: "Cancel" }));

    server.use(
      http.put("*/api/jobs/searches/:id", () =>
        HttpResponse.json(
          {
            detail:
              "Give your permission to search www.indeed.com in Find jobs first.",
          },
          { status: 422 },
        ),
      ),
    );
    await openSchedule(user, list, "Lyon");
    dialog = await screen.findByRole("dialog", {
      name: "Automatic search of Lyon",
    });
    await weekdaysOnly(user, dialog);
    expect(await within(dialog).findByRole("alert")).toHaveTextContent(
      "Not saved. Give your permission to search www.indeed.com in Find jobs first.",
    );
    expect(within(dialog).getByRole("button", { name: "Save" })).toBeEnabled();
    await user.click(within(dialog).getByRole("button", { name: "Cancel" }));
    expect(rowOf(list, "Lyon")).toHaveTextContent("Runs when you start it.");
  });

  it("schedules the main search from its row through the profile, with the time zone of every search", async () => {
    const profile = fakeProfile({ search: structuredClone(EMPTY.search) }, 6);
    fakeSearches({
      saved: [makeSearch(REMOTE, "Remote roles")],
      mainSchedule: () =>
        (profile.sections.search as { sweep_schedule?: object } | undefined)
          ?.sweep_schedule,
    });
    const { user, list } = await openFindJobs();
    await openSchedule(user, list, "Main search");
    const dialog = await screen.findByRole("dialog", {
      name: "Automatic search",
    });
    expect(within(dialog).getByLabelText("Time zone")).toBeInTheDocument();
    expect(dialog).toHaveTextContent("The app runs your main search by itself");
    await weekdaysOnly(user, dialog);
    expect(
      await screen.findByText(
        "Automatic search: on, weekdays at 06:17 (Europe/Paris).",
      ),
    ).toBeInTheDocument();
    expect(profile.sections.search).toMatchObject({
      sweep_schedule: { enabled: true, days: WEEKDAYS, times: ["06:17"] },
    });
    await waitFor(() => {
      expect(rowOf(list, "Main search")).toHaveTextContent(
        "Automatic: weekdays at 06:17 (Europe/Paris)",
      );
    });
  });
});

describe("The words of a saved search", () => {
  it("names a copy that is free, within 60 characters", () => {
    expect(copyName("Remote", ["Remote", "Remote copy"])).toBe("Remote copy 2");
    expect(copyName("x".repeat(60), [])).toHaveLength(60);
  });

  it("refuses an empty, long, main or taken name", () => {
    expect(nameProblem(" ", [])).toBe("Give this search a name.");
    expect(nameProblem("x".repeat(61), [])).toMatch(/60 characters/);
    expect(nameProblem("MAIN search", [])).toMatch(/main search/);
    expect(nameProblem("lyon", ["Lyon"])).toMatch(/already/);
    expect(nameProblem("Lyon", [])).toBeNull();
  });

  it("says what a search looks for, and reads an old sweep as the main search's", () => {
    expect(criteriaLine({})).toBe("Any title; Anywhere; on the web");
    expect(searchOf(sweep("old", undefined))).toBe("main");
    expect(searchOf(sweep("new", { id: REMOTE, name: "Remote roles" }))).toBe(
      REMOTE,
    );
  });

  it("says a run waits in line only when the api kept it in the line", () => {
    const request = {
      id: "request-1",
      search_id: REMOTE,
      place: 2,
      trigger: "manual",
      requested_at: "2026-11-26T09:00:00Z",
    };
    expect(runWords("Lyon", { outcome: "queued", request })).toBe(
      "Lyon waits in line: number 2.",
    );
    expect(runWords("Lyon", { outcome: "queued", request: null })).toBe(
      "Lyon waits in line.",
    );
    expect(runWords("Lyon", { outcome: "started", request: null })).toBe(
      "Lyon started.",
    );
    for (const outcome of [
      "no_websites",
      "search_invalid",
      "queue_down",
      "running",
      "not_found",
      "unknown",
    ]) {
      const words = runWords("Lyon", { outcome, request: null });
      expect(words).toMatch(/^Lyon did not start\. /);
      expect(words).not.toMatch(/line/);
    }
  });

  it("says what a save that stopped halfway left: the search, each permission given and not taken back", () => {
    const base = { name: "Lyon", untried: [], conflict: false };
    expect(
      halfSavedWords({
        ...base,
        given: [],
        written: true,
        failed: { platform: "welcome_to_the_jungle", refused: true },
        untried: ["indeed"],
        reason: "Unavailable.",
      }),
    ).toBe(
      "Lyon is saved, but the Welcome to the Jungle and Indeed permissions were not taken back. Unavailable. Try Save again.",
    );
    expect(
      halfSavedWords({
        ...base,
        given: ["linkedin"],
        written: true,
        failed: { platform: "indeed", refused: false },
        untried: ["freework"],
        reason: "The app cannot reach the server.",
        conflict: true,
      }),
    ).toBe(
      "Lyon is saved and the LinkedIn permission is given, but taking back the Indeed permission was not confirmed, and the Free-Work permission was not taken back. The app cannot reach the server. Load current settings, then save again.",
    );
    expect(
      halfSavedWords({
        ...base,
        given: ["indeed", "freework"],
        written: false,
        reason: "Too many titles.",
      }),
    ).toBe(
      "The Indeed and Free-Work permissions are given, but Lyon was not saved. Too many titles.",
    );
    expect(
      halfSavedWords({
        ...base,
        given: ["indeed"],
        written: null,
        reason: "The server is slow to answer. Try again.",
        conflict: true,
      }),
    ).toBe(
      "The Indeed permission is given, but Lyon may not be saved. The server is slow to answer. Try again. Load current settings, then save again.",
    );
    expect(
      halfSavedWords({
        ...base,
        given: ["indeed"],
        written: false,
        failed: { platform: "linkedin", refused: true },
        reason: "Type the words to search.",
      }),
    ).toBe(
      "The Indeed permission is given, but Lyon was not saved: the LinkedIn permission was not given. Type the words to search.",
    );
  });
});
