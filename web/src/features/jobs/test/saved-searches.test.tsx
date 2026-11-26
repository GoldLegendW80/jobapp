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
  lastRunOf,
  nameProblem,
  runWords,
} from "@/features/jobs/helpers/saved-searches";
import { admin, fakeSession } from "@/test/auth";
import { fakeJobsApi, makeJob } from "@/test/jobs";
import { fakeProfile } from "@/test/profile";
import { renderApp } from "@/test/render";
import { fakeSearches, makeSearch } from "@/test/searches";
import { emptySweeps, server } from "@/test/server";

const REMOTE = "00000000-0000-4000-8000-000000000001";
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
  renderApp("/jobs");
  await screen.findByRole("list", { name: "Postings" });
  const user = userEvent.setup();
  await user.click(screen.getByRole("button", { name: "Find jobs" }));
  const list = await screen.findByRole("list", { name: "Your searches" });
  return { user, list };
}

function rowOf(list: HTMLElement, name: string) {
  return within(list).getByRole("listitem", { name });
}

describe("Your searches (jobs J33)", () => {
  it("lists the main search first, then each saved one with what it searches, its schedule and last run", async () => {
    fakeSearches({
      main: () => ({ title_include: TERMS(["Data engineer"]) }),
      saved: [
        makeSearch(REMOTE, "Remote roles", {
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
        }),
      ],
    });
    server.use(
      http.get("*/api/jobs/sweeps", () =>
        HttpResponse.json({
          ...emptySweeps,
          items: [sweep("s1", { id: REMOTE, name: "Remote roles" })],
        }),
      ),
    );
    const { list } = await openFindJobs();
    const rows = within(list).getAllByRole("listitem");
    expect(
      rows.map((row) => within(row).getByRole("heading").textContent),
    ).toEqual(["Main search", "Remote roles"]);
    const remote = rowOf(list, "Remote roles");
    expect(remote).toHaveTextContent("ML engineer; Lyon; on Indeed");
    expect(remote).toHaveTextContent("Runs when you start it.");
    expect(remote).toHaveTextContent(/Last run .*, 3 new postings\./);
    expect(rowOf(list, "Main search")).toHaveTextContent("Not run yet.");
    expect(
      screen.getByRole("link", { name: "Settings, Search" }),
    ).toHaveAttribute("href", "/settings/search");
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
    expect(lastRunOf("main", [sweep("old", undefined)])?.id).toBe("old");
    expect(lastRunOf(REMOTE, [sweep("old", undefined)])).toBeUndefined();
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
